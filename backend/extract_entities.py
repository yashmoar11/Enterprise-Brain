"""
extract_entities.py — Entity extraction only (no re-embedding)

Reads existing Chunk nodes from Neo4j, runs LLMGraphTransformer on their
text content, and stores the resulting entity graph.  Use this when
embeddings are already ingested but the knowledge graph is empty.

Usage:
  python extract_entities.py --dataset book
  python extract_entities.py --dataset papers_energy_sustainability
  python extract_entities.py --dataset book --batch-size 15   # smaller if hitting rate limits
"""

import os
import sys
import time
import argparse
from dotenv import load_dotenv

load_dotenv()

from langchain_neo4j import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_core.documents import Document

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemini-2.5-pro")

DOMAIN_SCHEMAS = {
    "ml": {
        "nodes": [
            "Concept", "Algorithm", "OptimizationTechnique", "ModelArchitecture",
            "SoftwareFramework", "Hardware", "PerformanceMetric", "Person", "Organization"
        ],
        "relationships": [
            "OPTIMIZES", "IMPLEMENTS", "SERVES", "USES", "PART_OF",
            "IMPROVES", "REQUIRES", "MEASURES", "RELATES_TO", "DEVELOPED_BY"
        ],
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
        ],
    },
}

# Map dataset_id → domain schema
DATASET_DOMAIN = {
    "book":                         "ml",
    "papers_energy_sustainability": "research",
    "papers_serious_games":         "research",
    "papers_hci_ubicomp":           "research",
}


def extract_entities(dataset_id: str, batch_size: int = 20, sleep_time: int = 62, start_batch: int = 1, model: str = GEMINI_MODEL):
    domain = DATASET_DOMAIN.get(dataset_id, "research")
    schema = DOMAIN_SCHEMAS.get(domain)

    print(f"\n=== Entity Extraction: '{dataset_id}' (domain: {domain}) ===")

    # 1. Read existing Chunk text from Neo4j
    graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)

    rows = graph.query(
        "MATCH (c:Chunk {dataset_id: $dataset_id}) RETURN c.text AS text, elementId(c) AS eid",
        params={"dataset_id": dataset_id}
    )
    if not rows:
        print(f"No Chunk nodes found for dataset '{dataset_id}'. Ingest documents first.")
        sys.exit(1)

    print(f"Found {len(rows)} chunks to process.")
    docs = [Document(page_content=r["text"], metadata={"dataset_id": dataset_id}) for r in rows if r["text"]]

    # 2. Build transformer
    print(f"Using model: {model}")
    llm = ChatGoogleGenerativeAI(temperature=0, model=model)
    if schema:
        transformer = LLMGraphTransformer(
            llm=llm,
            allowed_nodes=schema["nodes"],
            allowed_relationships=schema["relationships"],
        )
    else:
        transformer = LLMGraphTransformer(llm=llm)

    # 3. Process in batches
    all_graph_docs = []
    total_batches  = (len(docs) - 1) // batch_size + 1
    start_index    = (start_batch - 1) * batch_size

    if start_batch > 1:
        print(f"Resuming from batch {start_batch}/{total_batches} (skipping first {start_index} chunks)...")

    for i in range(start_index, len(docs), batch_size):
        batch      = docs[i : i + batch_size]
        batch_num  = i // batch_size + 1
        print(f"-> Batch {batch_num}/{total_batches}  ({len(batch)} chunks)...")

        try:
            batch_docs = transformer.convert_to_graph_documents(batch)
            all_graph_docs.extend(batch_docs)
            nodes_this_batch = sum(len(gd.nodes) for gd in batch_docs)
            rels_this_batch  = sum(len(gd.relationships) for gd in batch_docs)
            print(f"   Extracted: {nodes_this_batch} nodes, {rels_this_batch} relationships")
        except Exception as e:
            print(f"   WARNING: batch {batch_num} failed — {e}")

        if i + batch_size < len(docs):
            print(f"   Sleeping {sleep_time}s (rate limit)...")
            time.sleep(sleep_time)

    # 4. Store in Neo4j
    total_nodes = sum(len(gd.nodes) for gd in all_graph_docs)
    total_rels  = sum(len(gd.relationships) for gd in all_graph_docs)
    print(f"\nStoring {total_nodes} entity nodes and {total_rels} relationships...")

    graph.add_graph_documents(all_graph_docs, baseEntityLabel=True, include_source=True)
    print(f"=== Done: '{dataset_id}' entity graph stored ===\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",     required=True,                  help="Dataset ID (e.g. book)")
    parser.add_argument("--batch-size",  type=int,   default=20,         help="Chunks per LLM batch (default 20)")
    parser.add_argument("--sleep",       type=int,   default=62,         help="Seconds between batches (default 62 — protects against Gemini RPM limit)")
    parser.add_argument("--start-batch", type=int,   default=1,          help="Batch number to start from (default 1 — use to resume after failure)")
    parser.add_argument("--model",       type=str,   default=GEMINI_MODEL, help=f"Gemini model to use (default: {GEMINI_MODEL})")
    args = parser.parse_args()

    extract_entities(args.dataset, batch_size=args.batch_size, sleep_time=args.sleep, start_batch=args.start_batch, model=args.model)
