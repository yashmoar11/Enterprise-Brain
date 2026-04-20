from langchain_neo4j import Neo4jVector
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
from agent.state import AgentState
import asyncio
import os

# --- Dual-Model Configuration ---
# Generation: Pro model — full reasoning, high quality answers
# Grading:    Flash-Lite model — fast, cheap, binary yes/no relevance checks
GENERATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
GRADING_MODEL    = os.getenv("GEMINI_GRADING_MODEL", "gemini-2.5-flash-lite")

generation_llm = ChatGoogleGenerativeAI(model=GENERATION_MODEL, temperature=0)
grading_llm    = ChatGoogleGenerativeAI(model=GRADING_MODEL,    temperature=0)

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

# --- Retriever Cache ---
# Retrievers are expensive to initialise (they verify the index exists in Neo4j).
# We build one per dataset_id and cache it so repeated queries don't re-initialise.
_retriever_cache: dict = {}

SHARED_VECTOR_INDEX = "vector_index_book"  # Neo4j only allows one vector index per node+property

def _get_index_name(dataset_id: str) -> str:
    """
    All datasets share a single vector index (Neo4j limitation: one vector index
    per node label + property combination). Dataset isolation is enforced via
    retrieval_query filtering by dataset_id, not by separate indexes.
    """
    return SHARED_VECTOR_INDEX

def get_retriever(dataset_id: str):
    """
    Builds a hybrid retriever for the given dataset and caches it.

    Hybrid search = vector (HNSW semantic) + full-text (BM25 keyword), merged with RRF.
    This catches chunks that are semantically similar AND chunks with exact keyword matches —
    especially important for technical/academic content with precise terminology.

    ef_search=300 triples the default HNSW exploration factor (100), visiting more
    candidate nodes before stopping. Better recall at minimal speed cost at this data scale.

    Falls back to vector-only if no full-text index exists yet (e.g. older ingested data).
    """
    if dataset_id not in _retriever_cache:
        index_name    = _get_index_name(dataset_id)
        ft_index_name = f"{index_name}_ft"
        embeddings    = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

        # Filter retrieval to only return chunks belonging to this dataset.
        # All datasets share the same Chunk node label so without this filter,
        # vector search returns chunks from every dataset mixed together.
        retrieval_query = (
            f"WHERE node.dataset_id = '{dataset_id}' "
            f"RETURN node.text AS text, score, node {{.source, .dataset_id}} AS metadata"
        )

        try:
            # Hybrid: vector (HNSW) + full-text (BM25), merged with RRF
            neo4j_vector = Neo4jVector.from_existing_index(
                embeddings,
                url=NEO4J_URI,
                username=NEO4J_USERNAME,
                password=NEO4J_PASSWORD,
                index_name=index_name,
                search_type="hybrid",
                keyword_index_name=ft_index_name,
                retrieval_query=retrieval_query,
            )
            print(f"[retriever] '{dataset_id}' → hybrid (vector + BM25)")
        except Exception:
            # Full-text index doesn't exist yet — fall back to vector only
            neo4j_vector = Neo4jVector.from_existing_index(
                embeddings,
                url=NEO4J_URI,
                username=NEO4J_USERNAME,
                password=NEO4J_PASSWORD,
                index_name=index_name,
                retrieval_query=retrieval_query,
            )
            print(f"[retriever] '{dataset_id}' → vector-only (no full-text index found, re-ingest to enable hybrid)")

        _retriever_cache[dataset_id] = neo4j_vector.as_retriever(
            search_kwargs={
                "k":         10,     # wider net with hybrid (was 6)
                "ef_search": 300,    # triple the default HNSW exploration (was 100)
            }
        )
    return _retriever_cache[dataset_id]


# --- Grader Chain (built once, reused for all parallel calls) ---
class GradeDocuments(BaseModel):
    """Binary score for relevance check on retrieved documents."""
    binary_score: str = Field(description="Documents are relevant to the question, 'yes' or 'no'")

_grade_prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a grader assessing relevance of a retrieved document to a user question.
    If the document contains keyword(s) or semantic meaning related to the question, grade it as relevant.
    Give a binary score 'yes' or 'no' to indicate whether the document is relevant."""),
    ("human", "Retrieved document: \n\n {document} \n\n User question: {question}")
])

_grader_chain = _grade_prompt | grading_llm.with_structured_output(GradeDocuments)


# --- Node 1: Retrieve ---
def retrieve(state: AgentState):
    """
    Retrieves the top-k chunks from the correct dataset's vector index.
    dataset_id in state determines which Neo4j vector index is queried —
    complete isolation between datasets is guaranteed at the index level.
    """
    print("---RETRIEVE---")
    question   = state["question"]
    dataset_id = state.get("dataset_id", "default")
    print(f"   Dataset: {dataset_id} -> index: {_get_index_name(dataset_id)}")

    retriever = get_retriever(dataset_id)
    documents = retriever.invoke(question)
    return {"documents": documents, "question": question}


# --- Node 2: Grade Documents (parallel async, Flash-Lite) ---
async def _grade_one(doc, question: str) -> bool:
    """Grades a single document asynchronously. Returns True if relevant."""
    result = await _grader_chain.ainvoke(
        {"question": question, "document": doc.page_content}
    )
    return result.binary_score.strip().lower() == "yes"


def grade_documents(state: AgentState):
    """
    Grades all retrieved documents in PARALLEL using asyncio.gather().
    All grading calls fire simultaneously — total time ≈ one single call (~1-2s).
    Uses Flash-Lite — grading is a simple yes/no check, not reasoning.
    """
    print("---CHECK RELEVANCE (parallel)---")
    question  = state["question"]
    documents = state["documents"]

    async def _grade_all():
        tasks   = [_grade_one(doc, question) for doc in documents]
        results = await asyncio.gather(*tasks)
        return results

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            grades = loop.run_until_complete(_grade_all())
        else:
            grades = loop.run_until_complete(_grade_all())
    except RuntimeError:
        grades = asyncio.run(_grade_all())

    filtered_docs = []
    web_search    = "No"

    for doc, is_relevant in zip(documents, grades):
        if is_relevant:
            print("---GRADE: DOCUMENT RELEVANT---")
            filtered_docs.append(doc)
        else:
            print("---GRADE: DOCUMENT NOT RELEVANT---")

    if not filtered_docs:
        print("---ALL DOCUMENTS IRRELEVANT — triggering query rewrite---")
        web_search = "Yes"

    return {"documents": filtered_docs, "question": question, "web_search": web_search}


# --- Node 3: Transform Query ---
def transform_query(state: AgentState):
    """Rewrites the question to improve vector retrieval recall."""
    print("---TRANSFORM QUERY---")
    question = state["question"]

    re_write_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a question re-writer that converts an input question to a better version
        optimized for vectorstore retrieval. Reason about the underlying semantic intent."""),
        ("human", "Here is the initial question: \n\n {question} \n Formulate an improved question.")
    ])

    question_rewriter = re_write_prompt | grading_llm | StrOutputParser()
    better_question   = question_rewriter.invoke({"question": question})
    return {"question": better_question}


# --- Node 4: Generate ---
def generate(state: AgentState):
    """
    Generates the final answer from filtered relevant documents.
    Uses Pro model. 'final_answer' tag ensures only these tokens stream to frontend.
    """
    print("---GENERATE---")
    question  = state["question"]
    documents = state["documents"]

    prompt = ChatPromptTemplate.from_template("""Answer the question based only on the following context:
    {context}

    Question: {question}
    """)

    rag_chain = prompt | generation_llm.with_config({"tags": ["final_answer"]}) | StrOutputParser()

    context_text = "\n\n".join([doc.page_content for doc in documents])
    generation   = rag_chain.invoke({"context": context_text, "question": question})

    return {"documents": documents, "question": question, "generation": generation}
