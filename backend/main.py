from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from agent.graph import app as agent_app
from langchain_neo4j import Neo4jGraph
import json
import os
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

app = FastAPI(title="Graph RAG Agent API")

# Allow Next.js frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3005"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Cypher helpers ────────────────────────────────────────────────────────────
#
# LLMGraphTransformer stores entity names in `n.id` (not `n.name`).
# Both queries below use COALESCE(n.id, n.name, elementId(n)) so they work
# regardless of which property the transformer happened to populate.
#
_GRAPH_QUERY_FILTERED = """
    MATCH (d:Document {dataset_id: $dataset_id})-[:MENTIONS]->(n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
    WITH DISTINCT n LIMIT 120
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""

_GRAPH_QUERY_ALL = """
    MATCH (n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
    WITH n LIMIT 120
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""

# Fallback for datasets whose entities have no Document linkage yet (include_source=False).
# Shows only entities NOT linked to any Document — excludes entities from properly-ingested
# datasets (e.g. papers_energy_sustainability) so the graph doesn't bleed across datasets.
_GRAPH_QUERY_ORPHANED = """
    MATCH (n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
      AND NOT (:Document)-[:MENTIONS]->(n)
    WITH n LIMIT 120
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""

_STREAM_QUERY_ORPHANED = """
    MATCH (n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
      AND NOT (:Document)-[:MENTIONS]->(n)
    WITH n LIMIT 80
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""

_STREAM_QUERY_FILTERED = """
    MATCH (d:Document {dataset_id: $dataset_id})-[:MENTIONS]->(n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
    WITH DISTINCT n LIMIT 80
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""

_STREAM_QUERY_ALL = """
    MATCH (n)
    WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
    WITH n LIMIT 80
    OPTIONAL MATCH (n)-[r]->(m)
    WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
    RETURN
        elementId(n)                                AS source_id,
        COALESCE(n.id, n.name, elementId(n))        AS source_name,
        labels(n)                                   AS source_labels,
        type(r)                                     AS rel_type,
        elementId(m)                                AS target_id,
        COALESCE(m.id, m.name, elementId(m))        AS target_name,
        labels(m)                                   AS target_labels
"""


def _rows_to_graph(rows: list) -> dict:
    """Convert Neo4j query rows into {nodes, links} for the frontend."""
    nodes_map: dict = {}
    links: list     = []
    for row in rows:
        sid = row["source_id"]
        if sid and sid not in nodes_map:
            nodes_map[sid] = {
                "id":    sid,
                "label": row["source_name"] or sid,
                "type":  row["source_labels"][0] if row["source_labels"] else "Entity",
                "val":   1,
            }
        tid = row["target_id"]
        if tid and tid not in nodes_map:
            nodes_map[tid] = {
                "id":    tid,
                "label": row["target_name"] or tid,
                "type":  row["target_labels"][0] if row["target_labels"] else "Entity",
                "val":   1,
            }
        if sid and tid and row["rel_type"]:
            links.append({"source": sid, "target": tid, "label": row["rel_type"]})
    return {"nodes": list(nodes_map.values()), "links": links}


def _get_graph(dataset_id: str, filtered_query: str, orphaned_query: str, all_query: str) -> dict:
    graph  = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)
    # Tier 1: dataset has proper Document→Entity linkage (energy, hci once ingested, etc.)
    result = graph.query(filtered_query, params={"dataset_id": dataset_id})
    if not result:
        # Tier 2: dataset entities exist but have no Document linkage yet (book, games).
        # Show only orphaned entities — excludes bleed-over from properly-ingested datasets.
        result = graph.query(orphaned_query)
    if not result:
        # Tier 3: absolute last resort — show everything
        result = graph.query(all_query)
    return _rows_to_graph(result)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/graph")
async def get_entity_graph(dataset_id: str = "default"):
    """
    Returns the knowledge graph for a specific dataset.
    Filters by dataset_id via Chunk nodes — graphs never mix across datasets.
    """
    try:
        return _get_graph(dataset_id, _GRAPH_QUERY_FILTERED, _GRAPH_QUERY_ORPHANED, _GRAPH_QUERY_ALL)
    except Exception as e:
        return {"nodes": [], "links": [], "error": str(e)}


@app.get("/stream")
async def stream_agent(query: str, dataset_id: str = "default"):
    """
    Streams agent events to the client via SSE.
    dataset_id routes the query to the correct isolated vector index.
    """
    async def event_generator():
        inputs = {"question": query, "dataset_id": dataset_id}

        async for event in agent_app.astream_events(inputs, version="v1"):
            kind = event["event"]
            name = event["name"]

            # 1. Retrieved Documents — build graph overlay
            if kind == "on_chain_end" and name == "retrieve":
                data = event["data"].get("output")
                if data and "documents" in data:
                    docs = data["documents"]
                    try:
                        graph_payload = _get_graph(dataset_id, _STREAM_QUERY_FILTERED, _STREAM_QUERY_ORPHANED, _STREAM_QUERY_ALL)
                    except Exception:
                        graph_payload = {"nodes": [], "links": []}

                    # Add query node + retrieved chunk overlay
                    nodes_map = {n["id"]: n for n in graph_payload["nodes"]}
                    links     = graph_payload["links"]

                    nodes_map["__query__"] = {
                        "id":    "__query__",
                        "label": query[:40] + "…" if len(query) > 40 else query,
                        "type":  "Query",
                        "val":   3,
                    }
                    for i, doc in enumerate(docs):
                        chunk_id = f"__chunk_{i}__"
                        nodes_map[chunk_id] = {
                            "id":    chunk_id,
                            "label": doc.page_content[:40] + "…",
                            "type":  "Chunk",
                            "val":   1.5,
                        }
                        links.append({"source": "__query__", "target": chunk_id, "label": "RETRIEVED"})

                    yield {"event": "graph_update", "data": json.dumps(
                        {"nodes": list(nodes_map.values()), "links": links}
                    )}

            # 2. Pipeline stage events
            elif kind == "on_chain_start" and name == "retrieve":
                yield {"event": "status", "data": "stage:retrieve"}
            elif kind == "on_chain_start" and name == "grade_documents":
                yield {"event": "status", "data": "stage:grade"}
            elif kind == "on_chain_start" and name == "transform_query":
                yield {"event": "status", "data": "stage:rewrite"}
            elif kind == "on_chain_start" and name == "generate":
                yield {"event": "status", "data": "stage:generate"}

            # 3. Generation tokens
            elif kind == "on_chat_model_stream":
                if "final_answer" in event.get("tags", []):
                    chunk_obj = event["data"]["chunk"]
                    content   = chunk_obj.content
                    # gemini-3.1-pro-preview returns content as a list of blocks
                    # e.g. [{'type': 'text', 'text': '...'}] — extract text only
                    if isinstance(content, list):
                        content = "".join(
                            block.get("text", "") if isinstance(block, dict) else str(block)
                            for block in content
                        )
                    if content:
                        yield {"event": "message", "data": str(content)}

    return EventSourceResponse(event_generator())


@app.get("/datasets")
async def list_datasets():
    """
    Returns available dataset IDs by reading vector indexes from Neo4j.
    Uses SHOW INDEXES (Neo4j 5+) — filters out full-text (_ft) indexes.
    """
    try:
        graph  = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)
        # Discover datasets by querying distinct dataset_id values on Chunk nodes.
        # This is more reliable than inspecting index names — Neo4j only allows one
        # vector index per node label + property, so per-dataset indexes aren't viable.
        result = graph.query(
            "MATCH (c:Chunk) WHERE c.dataset_id IS NOT NULL "
            "RETURN DISTINCT c.dataset_id AS dataset_id ORDER BY dataset_id"
        )
        datasets = [row["dataset_id"] for row in result]
        return {"datasets": datasets if datasets else ["default"]}
    except Exception as e:
        return {"datasets": ["default"], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
