from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from agent.graph import app as agent_app
from langchain_community.graphs import Neo4jGraph
import json
import asyncio
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


@app.get("/graph")
async def get_entity_graph(dataset_id: str = "default"):
    """
    Returns the knowledge graph for a specific dataset.
    Filters by dataset_id via Chunk nodes — graphs never mix across datasets.
    """
    try:
        graph = Neo4jGraph(
            url=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=NEO4J_PASSWORD
        )

        # Find entity nodes connected to chunks that belong to this dataset.
        # Isolation guarantee: entities from dataset A never appear in dataset B.
        result = graph.query("""
            MATCH (chunk:Chunk {dataset_id: $dataset_id})<-[:MENTIONS|PART_OF*0..2]-(n)
            WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
            WITH DISTINCT n LIMIT 120
            OPTIONAL MATCH (n)-[r]->(m)
            WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
            RETURN
                elementId(n) AS source_id,
                n.name AS source_name,
                labels(n) AS source_labels,
                type(r) AS rel_type,
                elementId(m) AS target_id,
                m.name AS target_name,
                labels(m) AS target_labels
        """, params={"dataset_id": dataset_id})

        # Fallback for "default" dataset — older data may not have dataset_id tags
        if not result:
            result = graph.query("""
                MATCH (n)
                WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
                WITH n LIMIT 120
                OPTIONAL MATCH (n)-[r]->(m)
                WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
                RETURN
                    elementId(n) AS source_id,
                    n.name AS source_name,
                    labels(n) AS source_labels,
                    type(r) AS rel_type,
                    elementId(m) AS target_id,
                    m.name AS target_name,
                    labels(m) AS target_labels
            """)

        nodes_map = {}
        links = []

        for row in result:
            sid = row["source_id"]
            if sid and sid not in nodes_map:
                nodes_map[sid] = {
                    "id": sid,
                    "label": row["source_name"] or sid,
                    "type": row["source_labels"][0] if row["source_labels"] else "Entity",
                    "val": 1
                }
            tid = row["target_id"]
            if tid and tid not in nodes_map:
                nodes_map[tid] = {
                    "id": tid,
                    "label": row["target_name"] or tid,
                    "type": row["target_labels"][0] if row["target_labels"] else "Entity",
                    "val": 1
                }
            if sid and tid and row["rel_type"]:
                links.append({"source": sid, "target": tid, "label": row["rel_type"]})

        return {"nodes": list(nodes_map.values()), "links": links}
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
                        neo4j_graph = Neo4jGraph(
                            url=NEO4J_URI,
                            username=NEO4J_USERNAME,
                            password=NEO4J_PASSWORD
                        )
                        entity_result = neo4j_graph.query("""
                            MATCH (chunk:Chunk {dataset_id: $dataset_id})<-[:MENTIONS|PART_OF*0..2]-(n)
                            WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
                            WITH DISTINCT n LIMIT 80
                            OPTIONAL MATCH (n)-[r]->(m)
                            WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
                            RETURN
                                elementId(n) AS source_id,
                                n.name AS source_name,
                                labels(n) AS source_labels,
                                type(r) AS rel_type,
                                elementId(m) AS target_id,
                                m.name AS target_name,
                                labels(m) AS target_labels
                        """, params={"dataset_id": dataset_id})

                        # Fallback for untagged legacy data
                        if not entity_result:
                            entity_result = neo4j_graph.query("""
                                MATCH (n)
                                WHERE NOT 'Chunk' IN labels(n) AND NOT 'Document' IN labels(n)
                                WITH n LIMIT 80
                                OPTIONAL MATCH (n)-[r]->(m)
                                WHERE NOT 'Chunk' IN labels(m) AND NOT 'Document' IN labels(m)
                                RETURN
                                    elementId(n) AS source_id,
                                    n.name AS source_name,
                                    labels(n) AS source_labels,
                                    type(r) AS rel_type,
                                    elementId(m) AS target_id,
                                    m.name AS target_name,
                                    labels(m) AS target_labels
                            """)

                        nodes_map = {}
                        links = []
                        for row in entity_result:
                            sid = row["source_id"]
                            if sid and sid not in nodes_map:
                                nodes_map[sid] = {
                                    "id": sid,
                                    "label": row["source_name"] or sid,
                                    "type": row["source_labels"][0] if row["source_labels"] else "Entity",
                                    "val": 1
                                }
                            tid = row["target_id"]
                            if tid and tid not in nodes_map:
                                nodes_map[tid] = {
                                    "id": tid,
                                    "label": row["target_name"] or tid,
                                    "type": row["target_labels"][0] if row["target_labels"] else "Entity",
                                    "val": 1
                                }
                            if sid and tid and row["rel_type"]:
                                links.append({"source": sid, "target": tid, "label": row["rel_type"]})

                        # Add query node + retrieved chunk overlay
                        nodes_map["__query__"] = {
                            "id": "__query__",
                            "label": query[:40] + "..." if len(query) > 40 else query,
                            "type": "Query",
                            "val": 3
                        }
                        for i, doc in enumerate(docs):
                            chunk_id = f"__chunk_{i}__"
                            nodes_map[chunk_id] = {
                                "id": chunk_id,
                                "label": doc.page_content[:40] + "...",
                                "type": "Chunk",
                                "val": 1.5
                            }
                            links.append({"source": "__query__", "target": chunk_id, "label": "RETRIEVED"})

                        graph_payload = {"nodes": list(nodes_map.values()), "links": links}
                    except Exception:
                        graph_payload = {
                            "nodes": [{"id": "__query__", "label": query[:40], "type": "Query", "val": 3}] +
                                     [{"id": f"__chunk_{i}__", "label": doc.page_content[:40] + "...", "type": "Chunk", "val": 1.5}
                                      for i, doc in enumerate(docs)],
                            "links": [{"source": "__query__", "target": f"__chunk_{i}__", "label": "RETRIEVED"}
                                      for i, _ in enumerate(docs)]
                        }

                    yield {"event": "graph_update", "data": json.dumps(graph_payload)}

            # 2. Status Updates
            elif kind == "on_chain_start" and name in ["grade_documents", "transform_query", "generate"]:
                yield {"event": "status", "data": f"Agent is currently: {name}..."}

            # 3. Generation Tokens
            elif kind == "on_chat_model_stream":
                if "final_answer" in event.get("tags", []):
                    chunk_obj = event["data"]["chunk"]
                    content   = chunk_obj.content
                    if content:
                        yield {"event": "message", "data": str(content)}

    return EventSourceResponse(event_generator())


@app.get("/datasets")
async def list_datasets():
    """
    Returns the list of available dataset IDs by inspecting existing
    vector indexes in Neo4j. Frontend uses this to populate the selector.
    """
    try:
        graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)
        result = graph.query("CALL db.indexes() YIELD name WHERE name STARTS WITH 'vector_index' RETURN name")
        datasets = []
        for row in result:
            name = row["name"]
            if name == "vector_index":
                datasets.append("default")
            else:
                datasets.append(name.replace("vector_index_", ""))
        return {"datasets": datasets}
    except Exception as e:
        return {"datasets": ["default"], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
