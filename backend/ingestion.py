import os
import time
import pymupdf4llm
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_neo4j import Neo4jVector, Neo4jGraph
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.documents import Document
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL   = os.getenv("GEMINI_ENTITY_MODEL", "gemini-2.5-pro")  # separate from generation model

# --- Domain Schemas ---
DOMAIN_SCHEMAS = {
    "ml": {
        "nodes": [
            "Concept", "Algorithm", "OptimizationTechnique", "ModelArchitecture",
            "SoftwareFramework", "Hardware", "PerformanceMetric",
            "Person", "Organization"
        ],
        "relationships": [
            "OPTIMIZES", "IMPLEMENTS", "SERVES", "USES", "PART_OF",
            "IMPROVES", "REQUIRES", "MEASURES", "RELATES_TO", "DEVELOPED_BY"
        ]
    },
    "research": {
        "nodes": [
            "Paper", "Author", "Method", "Dataset", "Result",
            "Hypothesis", "ResearchField", "Institution", "Concept"
        ],
        "relationships": [
            "AUTHORED_BY", "CITES", "PROPOSES", "USES_DATASET",
            "AFFILIATED_WITH", "IMPROVES_UPON", "COLLABORATED_WITH",
            "EVALUATES_ON", "RELATES_TO"
        ]
    },
    "auto": None
}


def _get_index_name(dataset_id: str) -> str:
    if dataset_id == "default":
        return "vector_index"
    return f"vector_index_{dataset_id}"


def _load_and_chunk(file_path: str) -> list[Document]:
    """
    Loads a document and splits it into chunks.

    PDFs:
      - pymupdf4llm converts to structured Markdown, preserving section headers,
        table structure, and figure captions (lost entirely with raw PyPDFLoader).
      - MarkdownHeaderTextSplitter splits at section boundaries, not character count.
        Each chunk carries its header hierarchy as metadata (title, section, subsection).
      - RecursiveCharacterTextSplitter then splits any oversized sections further,
        keeping a 200-char overlap to avoid cutting mid-concept.

    Plain text (.txt):
      - Falls back to RecursiveCharacterTextSplitter since there are no headers.
    """
    if file_path.endswith(".pdf"):
        print("  Converting PDF → structured Markdown (pymupdf4llm)...")
        md_text = pymupdf4llm.to_markdown(file_path)

        # Split by markdown headers first — respects document structure
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#",   "title"),
                ("##",  "section"),
                ("###", "subsection"),
            ],
            strip_headers=False   # keep headers in chunk text for context
        )
        header_chunks = header_splitter.split_text(md_text)

        # Secondary split — break up any sections that are still too long
        secondary_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " "]
        )
        docs = secondary_splitter.split_documents(header_chunks)

        # Inject source file path (header splitter doesn't add it automatically)
        for doc in docs:
            doc.metadata["source"] = file_path

    else:
        loader = TextLoader(file_path)
        raw_documents = loader.load()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ".", " "]
        )
        docs = splitter.split_documents(raw_documents)

    return docs


def ingest_document(file_path: str, dataset_id: str = "default", domain: str = "ml"):
    """
    Ingests a document into Neo4j with hybrid retrieval support.

    Each dataset_id gets:
      - Its own vector index  (HNSW, for semantic search)
      - Its own full-text index (BM25, for keyword search)

    Both indexes are queried at retrieval time and merged with RRF.

    Args:
        file_path:  Path to a PDF or .txt file.
        dataset_id: Unique name for this dataset (e.g. "book", "papers_hci").
                    Determines which vector + full-text indexes to write to.
        domain:     Entity schema: "ml", "research", or "auto".
    """
    index_name    = _get_index_name(dataset_id)
    ft_index_name = f"{index_name}_ft"   # full-text index paired with the vector index

    print(f"--- STARTING INGESTION ---")
    print(f"    File:            {file_path}")
    print(f"    Dataset ID:      {dataset_id}")
    print(f"    Vector index:    {index_name}")
    print(f"    Full-text index: {ft_index_name}")
    print(f"    Domain:          {domain}")

    # 1. Load + chunk with structure-aware splitting
    docs = _load_and_chunk(file_path)
    print(f"Split into {len(docs)} chunks.")

    # 2. Tag every chunk with dataset_id for graph filtering
    for doc in docs:
        doc.metadata["dataset_id"] = dataset_id

    # 3. Neo4j connection
    graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)

    # 4. Vector index (HNSW semantic search)
    print(f"Creating vector index '{index_name}'...")
    embedding_model = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    embedding_batch = 500
    embedding_sleep = 2

    # Pre-create the named vector index BEFORE calling from_documents().
    # Without this, LangChain detects any existing Chunk index and reuses it
    # (e.g. vector_index_book), ignoring the index_name parameter entirely.
    graph.query(
        f"CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS "
        f"FOR (n:Chunk) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: 3072, `vector.similarity_function`: `cosine`}}}}"
    )

    neo4j_vector = None
    for i in range(0, len(docs), embedding_batch):
        batch = docs[i:i + embedding_batch]
        print(f"-> Embedding batch {i // embedding_batch + 1}/{(len(docs) - 1) // embedding_batch + 1}...")
        if neo4j_vector is None:
            neo4j_vector = Neo4jVector.from_documents(
                batch,
                embedding_model,
                url=NEO4J_URI,
                username=NEO4J_USERNAME,
                password=NEO4J_PASSWORD,
                index_name=index_name,
                node_label="Chunk",
                text_node_property="text",
                embedding_node_property="embedding"
            )
        else:
            neo4j_vector.add_documents(batch)

        if i + embedding_batch < len(docs):
            print(f"   Sleeping {embedding_sleep}s (embedding RPM limit)...")
            time.sleep(embedding_sleep)

    # 5. Full-text index (BM25 keyword search) — paired with the vector index
    # Both are queried at retrieval time and merged with RRF for hybrid search.
    print(f"Creating full-text index '{ft_index_name}'...")
    try:
        graph.query(
            f"CREATE FULLTEXT INDEX {ft_index_name} IF NOT EXISTS "
            f"FOR (n:Chunk) ON EACH [n.text]"
        )
        print(f"   Full-text index '{ft_index_name}' ready.")
    except Exception as e:
        print(f"   Full-text index note: {e}")

    # 6. Entity extraction (knowledge graph)
    print("Extracting graph entities...")
    llm    = ChatGoogleGenerativeAI(temperature=0, model=GEMINI_MODEL)
    schema = DOMAIN_SCHEMAS.get(domain, DOMAIN_SCHEMAS["auto"])

    if schema is not None:
        llm_transformer = LLMGraphTransformer(
            llm=llm,
            allowed_nodes=schema["nodes"],
            allowed_relationships=schema["relationships"]
        )
    else:
        llm_transformer = LLMGraphTransformer(llm=llm)

    graph_documents = []
    batch_size = 20
    sleep_time = 62   # Gemini RPM limit — 40 calls/batch (2 per chunk) needs breathing room

    print(f"Processing {len(docs)} chunks in batches of {batch_size}...")
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        print(f"-> Batch {i // batch_size + 1}/{(len(docs) - 1) // batch_size + 1}...")
        batch_graph_docs = llm_transformer.convert_to_graph_documents(batch)
        graph_documents.extend(batch_graph_docs)
        if i + batch_size < len(docs):
            print(f"   Sleeping {sleep_time}s (entity extraction RPM limit)...")
            time.sleep(sleep_time)

    graph.add_graph_documents(graph_documents, baseEntityLabel=True, include_source=True)

    # 7. Sequential chunk linking
    print("Linking chunks sequentially...")
    graph.query("""
        MATCH (c:Chunk) WHERE c.source = $source_file
        WITH c ORDER BY c.start_index ASC
        WITH collect(c) as chunk_list
        FOREACH (i in range(0, size(chunk_list)-2) |
            FOREACH (c1 in [chunk_list[i]] |
                FOREACH (c2 in [chunk_list[i+1]] |
                    MERGE (c1)-[:NEXT_CHUNK]->(c2)
                )
            )
        )
    """, params={"source_file": file_path})

    print(f"--- INGESTION COMPLETE: dataset='{dataset_id}', index='{index_name}' ---")


if __name__ == "__main__":
    import sys

    if not os.path.exists("data"):
        os.makedirs("data")

    if len(sys.argv) >= 3:
        file_to_ingest = sys.argv[1]
        dataset        = sys.argv[2]
        dom            = sys.argv[3] if len(sys.argv) >= 4 else "ml"
        if not os.path.exists(file_to_ingest):
            print(f"Error: File '{file_to_ingest}' not found.")
            sys.exit(1)
        ingest_document(file_to_ingest, dataset_id=dataset, domain=dom)
    else:
        print("Usage: python ingestion.py <file> <dataset_id> [domain]")
        print("Examples:")
        print("  python ingestion.py data/book.pdf        book    ml")
        print("  python ingestion.py data/hci_paper.pdf   papers_hci    research")
