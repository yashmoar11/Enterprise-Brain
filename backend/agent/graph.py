from langgraph.graph import END, StateGraph, START
from agent.nodes import retrieve, grade_documents, generate, transform_query
from agent.state import AgentState

def decide_to_generate(state):
    """
    Determines whether to generate an answer, or re-generate a question.
    """
    print("---DECISION LOGIC---")
    web_search = state["web_search"]
    
    if web_search == "Yes":
        # All documents were irrelevant -> Rewrite Query
        print("---DECISION: TRANSFORM QUERY---")
        return "transform_query"
    else:
        # Relevant documents exist -> Generate Answer
        print("---DECISION: GENERATE---")
        return "generate"

# Initialize Graph
workflow = StateGraph(AgentState)

# Add Nodes
workflow.add_node("retrieve", retrieve)
workflow.add_node("grade_documents", grade_documents)
workflow.add_node("generate", generate)
workflow.add_node("transform_query", transform_query)

# Add Edges
workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "grade_documents")

# Conditional Edge: The "Brain" of the operation
# This checks the output of 'grade_documents' and routes execution
workflow.add_conditional_edges(
    "grade_documents",
    decide_to_generate,
    {
        "transform_query": "transform_query",
        "generate": "generate",
    },
)

# Creating the Cycle: Transform -> Retrieve
workflow.add_edge("transform_query", "retrieve")
workflow.add_edge("generate", END)

# Compile
app = workflow.compile()
