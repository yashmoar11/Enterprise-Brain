from typing import TypedDict, List, Optional
from langchain_core.documents import Document

class AgentState(TypedDict):
    """
    Represents the internal state of the graph.
    """
    question: str                   # The user's original question
    documents: List[Document]       # The currently retrieved documents
    generation: str                 # The final generated answer
    web_search: str                 # Binary flag ("Yes"/"No") to trigger fallback
    retry_count: int                # Counter to prevent infinite loops
    steps: List[str]                # Audit log of steps taken
    dataset_id: str                 # Which vector index to query ("book", "papers", etc.)
