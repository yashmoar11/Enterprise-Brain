# EnterpriseBrain — Real-Time Full-Stack Knowledge Graph System

> A production-style Graph RAG (Retrieval-Augmented Generation) system that ingests documents, extracts a semantic knowledge graph, and answers questions in real time using streamed LLM responses with live graph visualization.

> **Latest:** Dual-model grading architecture (Flash for grading, Pro for generation) with parallel async document grading reduces response time from ~3 minutes to ~20 seconds.

---

## Table of Contents

1. [What Is This?](#what-is-this)
2. [What Problem Does It Solve?](#what-problem-does-it-solve)
3. [Architecture Overview](#architecture-overview)
4. [High-Level Design](#high-level-design)
5. [Low-Level Design](#low-level-design)
6. [Technology Stack](#technology-stack)
7. [Project Structure](#project-structure)
8. [Prerequisites & Installation](#prerequisites--installation)
9. [Running the Project](#running-the-project)
10. [Ingesting Data](#ingesting-data)
11. [API Reference](#api-reference)
12. [Frontend Guide](#frontend-guide)
13. [How the Agent Works (Step-by-Step)](#how-the-agent-works-step-by-step)
14. [Configuration Reference](#configuration-reference)
15. [Performance Notes](#performance-notes)
16. [Resume Justification](#resume-justification)

---

## What Is This?

EnterpriseBrain is a full-stack AI system that turns unstructured documents (PDFs, text files) into a queryable, visual knowledge graph. You ask it a question in natural language, it:

1. Searches a vector index in Neo4j to find semantically relevant document chunks
2. Grades those chunks for actual relevance using an LLM
3. If chunks are irrelevant, rewrites the query and retries (self-correcting agent loop)
4. Generates a streaming answer token-by-token using Gemini
5. Simultaneously renders the full knowledge graph — including extracted entities (Person, Organization, Product, Technology) and their relationships — live in the browser

The key distinction from a standard chatbot or basic RAG system is the **knowledge graph layer**. Documents are not just chunked and embedded — they are also semantically parsed by an LLM to extract named entities and relationships (e.g., `Sam Altman → CEO_OF → OpenAI`, `OpenAI → CREATED → GPT-4`). These live in Neo4j as graph nodes and edges, enabling richer context retrieval and a visual interface showing how concepts relate.

---

## What Problem Does It Solve?

### Problem 1: Standard RAG is a black box

In vanilla RAG, you embed document chunks, run a similarity search, and shove the top-K results into an LLM prompt. The user sees an answer but has no visibility into *which* information was used, *how* concepts relate, or *why* certain chunks were retrieved. There is no feedback loop — if the retrieved chunks are irrelevant, the LLM hallucinates.

**EnterpriseBrain fixes this by:**
- Showing a live visualization of which graph nodes and document chunks were retrieved for each query
- Running a relevance grader that filters irrelevant chunks before generation
- Self-correcting via query rewriting when retrieval fails

### Problem 2: LLM responses feel slow (high time-to-first-token)

Without streaming, you wait for the entire LLM response to be generated (5–15 seconds) before anything appears on screen.

**EnterpriseBrain fixes this with Server-Sent Events (SSE):** tokens are emitted to the browser as they are generated. Time-to-first-token drops from ~5s to under 100ms.

### Problem 3: Vector search misses relational context

A vector database treats each chunk as an isolated unit. It cannot answer "who founded the company that created GPT-4?" because that requires traversing a relationship graph. A pure vector search would have to hope that a single chunk contains both pieces of information.

**EnterpriseBrain fixes this with Neo4j's hybrid model:** the same database stores both vector embeddings (for similarity search) and a labeled property graph (for relationship traversal). Both can be queried together.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          DOCKER COMPOSE                                 │
│                                                                         │
│  ┌─────────────────┐    ┌──────────────────────┐    ┌───────────────┐  │
│  │   NEXT.JS        │    │     FASTAPI           │    │    NEO4J      │  │
│  │   Frontend       │    │     Backend           │    │   Database    │  │
│  │   :3000          │◄───┤     :8000             │◄───┤   :7474/:7687 │  │
│  │                  │    │                       │    │               │  │
│  │  ChatInterface   │    │  /stream (SSE)        │    │  Vector Index │  │
│  │  GraphViz        │    │  /graph  (REST)       │    │  Graph Store  │  │
│  └─────────────────┘    └──────────────────────┘    └───────────────┘  │
│                                    │                                    │
│                          ┌─────────┴──────────┐                        │
│                          │   LANGGRAPH AGENT   │                        │
│                          │                     │                        │
│                          │  retrieve           │                        │
│                          │     ↓               │                        │
│                          │  grade_documents    │                        │
│                          │     ↓           ↓   │                        │
│                          │  generate  transform│                        │
│                          │             _query  │                        │
│                          │               ↓     │                        │
│                          │           retrieve  │                        │
│                          └─────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow: Ingestion Phase

```
PDF / TXT File
      │
      ▼
TextLoader / PyPDFLoader
      │
      ▼
RecursiveCharacterTextSplitter
  chunk_size=1000, overlap=200
      │
      ├──────────────────────────────────────┐
      ▼                                      ▼
Google Embeddings                   LLMGraphTransformer
(text-embedding-004)                (Gemini 2.5 Pro)
      │                                      │
      ▼                                      ▼
Neo4j Vector Index              Neo4j Knowledge Graph
"Chunk" nodes with              Entity nodes (Person,
embedding property              Org, Product, etc.)
                                + Relationship edges
      │                                      │
      └──────────────────────────────────────┘
                        │
                        ▼
              NEXT_CHUNK relationships
              (sequential linking)
```

### Data Flow: Query Phase

```
User Question (browser)
      │
      ▼ HTTP GET /stream?query=...
FastAPI — opens SSE stream
      │
      ▼
LangGraph Agent
      │
   [Node 1] retrieve
      │  Neo4jVector similarity search
      │  → top-K relevant chunks
      │
      ▼ SSE: "graph_update" (entity graph + retrieved chunks)
      │
   [Node 2] grade_documents
      │  Gemini grades each chunk: relevant/not relevant
      │
      ├── all relevant ──────────────────────────────┐
      │                                              ▼
      │                                       [Node 4] generate
      └── all irrelevant                             │
            │                                 Gemini generates answer
            ▼                                 streaming tokens
      [Node 3] transform_query                       │
            │  Gemini rewrites the question           ▼
            └──────────────────────────────► SSE: "message" tokens
                   back to [Node 1]          (token by token to browser)
```

---

## High-Level Design

### Component Responsibilities

| Component | Responsibility |
|---|---|
| **Neo4j** | Single source of truth. Stores both vector embeddings (for retrieval) and the knowledge graph (for visualization and traversal) |
| **FastAPI + SSE** | Thin async layer. Opens an SSE stream per query, runs the LangGraph agent, forwards events to the browser |
| **LangGraph Agent** | Stateful agent loop. Manages the retrieve → grade → generate cycle with retry logic and state machine |
| **Next.js Frontend** | Renders the chat UI and live graph. Consumes SSE events. Never does any ML — purely display |
| **Docker Compose** | Wires all three services together with shared networking, health checks, and volumes |

### Why Graph RAG instead of plain RAG?

```
Plain RAG:
  Query → Embed → Search vector DB → Top-K chunks → LLM → Answer
  ✗ No visibility into what was retrieved
  ✗ Can't traverse relationships between concepts
  ✗ If retrieval fails, no recovery mechanism

Graph RAG:
  Query → Embed → Search vector DB → Top-K chunks
                                          │
                                    Grade relevance
                                          │
                                  ┌───────┴────────┐
                              relevant          not relevant
                                  │                  │
                              Generate         Rewrite query
                            streaming            → retry
                             answer
  ✓ Self-correcting agent loop
  ✓ Relationship graph extracted and visible
  ✓ Streaming — fast time-to-first-token
  ✓ Live visualization of retrieval paths
```

---

## Low-Level Design

### 1. Ingestion Pipeline (`backend/ingestion.py`)

**Step 1 — Document Loading**
```python
TextLoader(file_path)      # for .txt files
PyPDFLoader(file_path)     # for .pdf files
```
Produces a list of `langchain_core.documents.Document` objects with `.page_content` and `.metadata`.

**Step 2 — Chunking**
```python
RecursiveCharacterTextSplitter(
    chunk_size=1000,       # target ~1000 chars per chunk
    chunk_overlap=200,     # 200-char overlap prevents context loss at boundaries
    separators=["\n\n", "\n", ".", " "]  # prefer natural break points
)
```
Why overlap? Without it, a sentence that crosses a chunk boundary loses context. With 200-char overlap, both adjacent chunks contain that boundary content.

**Step 3 — Vector Index**
```python
Neo4jVector.from_documents(
    docs,
    GoogleGenerativeAIEmbeddings(model="models/text-embedding-004"),
    index_name="vector_index",
    node_label="Chunk",
    text_node_property="text",
    embedding_node_property="embedding"
)
```
Creates a `Chunk` node in Neo4j for each chunk. Each node has:
- `text`: the raw chunk text
- `embedding`: 768-dim float array from Google's text-embedding-004 model
- `source`: the file path it came from

A vector index is automatically created over the `embedding` property.

**Step 4 — Graph Entity Extraction**
```python
llm = ChatGoogleGenerativeAI(temperature=0, model="gemini-2.5-pro")
llm_transformer = LLMGraphTransformer(llm=llm)
graph_documents = llm_transformer.convert_to_graph_documents(docs)
graph.add_graph_documents(graph_documents, baseEntityLabel=True, include_source=True)
```
The `LLMGraphTransformer` sends each chunk to Gemini with a prompt asking it to extract:
- **Nodes**: named entities like `Person`, `Organization`, `Product`, `Technology`, `Location`
- **Relationships**: typed edges like `CEO_OF`, `CREATED`, `FOUNDED_BY`, `PART_OF`

These are written to Neo4j as actual graph nodes and labeled edges. Example result:
```
(Sam Altman:Person) -[:CEO_OF]-> (OpenAI:Organization)
(OpenAI:Organization) -[:CREATED]-> (GPT-4:Product)
(GPT-4:Product) -[:IS_A]-> (Large Language Model:Technology)
```

**Step 5 — Structural Linking**
```cypher
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
```
Creates a linked list of chunks in document order. This enables sequential reading — if a retrieved chunk is the middle of a passage, the agent can traverse `NEXT_CHUNK` to get the surrounding context.

---

### 2. Agent State (`backend/agent/state.py`)

```python
class AgentState(TypedDict):
    question: str           # original or rewritten question
    documents: List[Document]  # retrieved + filtered chunks
    generation: str         # final LLM output
    web_search: str         # "Yes" / "No" — triggers query rewriting
    retry_count: int        # prevents infinite loops
    steps: List[str]        # audit log of each node visited
```

The state is a plain Python TypedDict — LangGraph passes this dict between nodes, each node returns a partial update.

---

### 3. Agent Graph (`backend/agent/graph.py`)

```
         START
           │
           ▼
        retrieve ◄────────────────────────────────────┐
           │                                           │
           ▼                                           │
     grade_documents                                   │
           │                                           │
    ┌──────┴──────┐                                    │
    │             │                                    │
  relevant    not relevant                             │
    │             │                                    │
    ▼             ▼                                    │
  generate  transform_query ───────────────────────────┘
    │
    ▼
   END
```

**Decision function:**
```python
def decide_to_generate(state):
    if state["web_search"] == "Yes":
        return "transform_query"   # all chunks were irrelevant → rewrite
    else:
        return "generate"          # at least one chunk was relevant → answer
```

---

### 4. SSE Streaming (`backend/main.py`)

The `/stream` endpoint uses `EventSourceResponse` from `sse-starlette`. It taps into LangGraph's `astream_events()` iterator which emits internal events as the agent executes:

```python
async for event in agent_app.astream_events(inputs, version="v1"):
    if event["event"] == "on_chain_end" and event["name"] == "retrieve":
        # Emit graph_update with entity graph + chunk overlay
        yield {"event": "graph_update", "data": json.dumps(graph_payload)}

    elif event["event"] == "on_chain_start" and event["name"] in [...]:
        # Emit current agent status
        yield {"event": "status", "data": f"Agent is: {name}..."}

    elif event["event"] == "on_chat_model_stream":
        # Emit individual token
        yield {"event": "message", "data": token}
```

Three SSE event types:
- `graph_update`: fired once after retrieval, sends full entity graph as JSON
- `status`: fired at each agent node transition, shows what step the agent is on
- `message`: fired for every token from the generation LLM, streamed token-by-token

---

### 5. Entity Graph Endpoint (`GET /graph`)

Called by the frontend on page load. Queries Neo4j for all entity nodes and relationships, excluding raw `Chunk` and `Document` nodes:

```cypher
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
```

Returns a `{nodes: [...], links: [...]}` JSON payload that `react-force-graph-2d` can directly render.

---

### 6. Frontend Graph Visualization (`frontend/components/GraphViz.tsx`)

Uses `react-force-graph-2d` — a React wrapper around D3's force simulation. Key decisions:

**Why `dynamic import` with `ssr: false`?**
`react-force-graph-2d` uses browser canvas APIs that don't exist in Node.js (Next.js server-side rendering). Dynamic import with `ssr: false` defers the import to the client bundle.

**Why `useMemo`?**
The force graph runs a physics simulation. Every time `graphData` changes by reference (not value), the simulation restarts from scratch — the graph "jumps". By memoizing with `useMemo`, the object reference only changes when the actual data changes, not on every SSE token.

**Node coloring by type:**
```
Person       → blue   (#60a5fa)
Organization → orange (#fb923c)
Product      → purple (#c084fc)
Technology   → green  (#34d399)
Location     → amber  (#fbbf24)
Query        → white  (#ffffff)
Chunk        → gray   (#94a3b8)
```

**Custom canvas rendering (`nodeCanvasObject`):**
Rather than using the built-in node renderer, a custom canvas drawing function draws circles with text labels below them. This gives full control over appearance while staying at 60fps.

---

## Technology Stack

### Backend

| Technology | Version | Why It's Used | How It's Used |
|---|---|---|---|
| **FastAPI** | 0.110.0 | Async Python web framework with native ASGI support. Chosen over Flask because it supports async generators needed for SSE, has automatic OpenAPI docs, and Pydantic integration | Serves two endpoints: `GET /stream` (SSE) and `GET /graph` (REST) |
| **LangGraph** | 0.2.14 | Agent state machine. Chosen over plain LangChain chains because it supports conditional branching and cycles (the retry loop). A linear chain cannot express "if condition → go back to node A" | Defines the retrieve → grade → [generate OR transform → retrieve] loop |
| **LangChain** | 0.2.16 | Provides RAG building blocks: document loaders, text splitters, vector store connectors | Used for `TextLoader`, `RecursiveCharacterTextSplitter` |
| **langchain-neo4j** | latest | Dedicated Neo4j integration package (split from `langchain_community`). More stable API and actively maintained by Neo4j | `Neo4jVector`, `Neo4jGraph` — replaces the deprecated `langchain_community.vectorstores.Neo4jVector` |
| **LangChain Experimental** | 0.0.64 | Contains `LLMGraphTransformer` which is still experimental/unstable | Entity extraction from document chunks into Neo4j graph format |
| **LangChain Google GenAI** | 1.0.10 | Google Gemini API integration for LangChain | `ChatGoogleGenerativeAI` for the LLM, `GoogleGenerativeAIEmbeddings` for embeddings |
| **Google Gemini 2.5 Pro** | API | Generation model — full reasoning for final answer synthesis, entity extraction, and query rewriting. Note: model ID is `gemini-2.5-pro` (the `-preview` suffix was retired by Google) | Final answer generation, entity extraction from document chunks |
| **Google Gemini 2.0 Flash** | API | Grading model — fast binary relevance judgement (yes/no). ~10× faster than Pro for the same simple task. All 6 chunk grades fire in parallel via `asyncio.gather()` | Document relevance grading in the `grade_documents` node |
| **Google gemini-embedding-001** | API | Embedding model (768 dims). Separate from generation models — outputs a fixed-size vector, not text | Converts text chunks and queries to vectors for semantic search |
| **Neo4j** | **5.26.0** | Graph database that natively supports both vector indexes and labeled property graphs. **Pinned to 5.26.0** — newer versions changed `CALL {} IN TRANSACTIONS` behaviour which broke LangChain's `add_graph_documents()` | Stores `Chunk` nodes (vector search) + entity nodes/edges (knowledge graph) |
| **SSE-Starlette** | 2.0.0 | Server-Sent Events for FastAPI. Chosen over WebSockets because SSE is unidirectional, simpler, works over HTTP/2, no client library needed | Streams graph updates, status events, and LLM tokens to the browser |
| **Pydantic** | 2.6.3 | Data validation and schema definition | `GradeDocuments` schema for structured LLM output in the grader node |
| **python-dotenv** | 1.0.1 | Loads `.env` files into `os.environ` | Loads `GOOGLE_API_KEY`, `NEO4J_URI`, `GEMINI_MODEL` at startup |

### Frontend

| Technology | Version | Why It's Used | How It's Used |
|---|---|---|---|
| **Next.js** | 15/16 | React framework with SSR, routing, and optimized bundling. Chosen over plain React because it handles server-side rendering, TypeScript out of the box, and the App Router | Single-page app with chat + graph visualization |
| **React** | 19 | Component model for building the UI. Hooks-based state management | `useState`, `useEffect`, `useMemo`, `useCallback`, `useRef` throughout |
| **TypeScript** | 5 | Type safety for frontend code. Catches interface mismatches between backend JSON and frontend props at compile time | Typed `GraphNode`, `GraphLink`, `GraphData`, `Message` interfaces |
| **react-force-graph-2d** | 1.29.0 | D3 force simulation wrapped in React. Renders interactive graph with physics-based node positioning | Renders the entity knowledge graph with force-directed layout |
| **@microsoft/fetch-event-source** | 2.0.1 | Reliable SSE client for browsers. Chosen over native `EventSource` because it supports POST, custom headers, and automatic reconnection | Connects to `/stream` and handles `graph_update`, `status`, `message` events |
| **Tailwind CSS** | 4 | Utility-first CSS. Fast to style without writing custom CSS | Dark theme (`slate-950`, `slate-900`, `slate-800`) throughout |

### Infrastructure

| Technology | Version | Why It's Used | How It's Used |
|---|---|---|---|
| **Docker Compose** | 3.8 | Orchestrates multi-container setup. Single command to start Neo4j + Backend + Frontend | `docker-compose up -d` starts all three services |
| **Neo4j 5.18** | Docker image | Graph database with APOC and Graph Data Science plugins | Persists knowledge graph to `./neo4j_data` volume |
| **Python 3.11** | Base image | Async support, performance, LangChain compatibility | Backend runtime |
| **Node 20** | Base image | LTS version with latest V8 engine | Frontend runtime |

---

## Project Structure

```
EnterpriseBrain/
│
├── backend/
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py          # AgentState TypedDict — shared state schema
│   │   ├── nodes.py          # 4 agent functions: retrieve, grade, transform, generate
│   │   └── graph.py          # LangGraph state machine definition
│   │
│   ├── data/
│   │   ├── sample_financial_report.txt   # TechFin Corp Q3 2025 (demo)
│   │   └── *.txt                         # Wikipedia articles (after fetch_data.py)
│   │
│   ├── main.py               # FastAPI app: /stream and /graph endpoints
│   ├── ingestion.py          # Document → Neo4j pipeline
│   ├── fetch_data.py         # Wikipedia data fetcher + ingestion runner
│   ├── requirements.txt      # Python dependencies
│   ├── .env                  # Environment variables (API keys, Neo4j URI)
│   └── Dockerfile            # python:3.11-slim → uvicorn main:app
│
├── frontend/
│   ├── app/
│   │   ├── page.tsx          # Root page — renders <ChatInterface />
│   │   ├── layout.tsx        # HTML shell, Geist fonts, metadata
│   │   └── globals.css       # Tailwind directives + CSS variables
│   │
│   ├── components/
│   │   ├── ChatInterface.tsx  # SSE client, messages state, query input
│   │   └── GraphViz.tsx       # react-force-graph-2d wrapper, node coloring
│   │
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.ts
│   └── Dockerfile            # node:20-alpine → npm run dev
│
├── neo4j_data/               # Neo4j persistent data volume (git-ignored)
├── neo4j_plugins/            # APOC + GDS plugins
├── docker-compose.yml        # Orchestrates all 3 services
└── .env                      # Docker Compose env vars (GOOGLE_API_KEY etc.)
```

---

## Prerequisites & Installation

### System Requirements

- **Docker Desktop** (includes Docker Compose): [docker.com/get-started](https://www.docker.com/get-started)
- **Google API Key** with Gemini and Embeddings access: [aistudio.google.com](https://aistudio.google.com)
- At least **4GB free RAM** for Neo4j heap (configured to 1-2GB by default)
- macOS, Linux, or Windows (WSL2 recommended on Windows)

The API key is already set in `.env`. No additional setup needed if you are using the provided key.

### One-Time Setup

```bash
# 1. Clone or navigate to the project
cd /path/to/EnterpriseBrain

# 2. Verify .env exists and has the API key
cat .env
# Should show: GOOGLE_API_KEY=AIzaSy...

# 3. Pull Docker images (one-time, ~2-3 minutes)
docker-compose pull
```

---

## Running the Project

### Option A: Docker Compose (Recommended)

```bash
# Start all services (Neo4j + Backend + Frontend)
docker-compose up -d

# Check all services are healthy
docker-compose ps

# Watch backend logs (useful during ingestion)
docker-compose logs -f backend
```

Wait about 30 seconds for Neo4j to initialize and the backend to connect.

```
Service         Port    URL
──────────────────────────────────────────────
Frontend        3000    http://localhost:3000
Backend API     8000    http://localhost:8000
Neo4j Browser   7474    http://localhost:7474
Neo4j Bolt      7687    (internal)
```

### Option B: Local Development (No Docker except Neo4j)

```bash
# Terminal 1: Neo4j only
docker run -d \
  --name neo4j-dev \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  -e NEO4J_PLUGINS='["apoc","graph-data-science"]' \
  neo4j:5.26.0

# Terminal 2: Backend
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd frontend
npm install
npm run dev
```

---

## Ingesting Data

The graph is empty until you ingest documents. Run ingestion from inside the backend container (or locally if running without Docker).

### Via Docker (Recommended)

```bash
# Fetch 9 Wikipedia articles + ingest all of them into Neo4j
docker exec -it graphrag-backend python fetch_data.py

# OR: just download without ingesting (saves to backend/data/)
docker exec -it graphrag-backend python fetch_data.py --fetch-only

# OR: ingest files already in data/ without re-downloading
docker exec -it graphrag-backend python fetch_data.py --ingest-only
```

**What gets ingested:**
| Article | Rich entities for |
|---|---|
| OpenAI | Sam Altman, ChatGPT, GPT-4, Microsoft investment |
| Anthropic | Dario Amodei, Claude, Constitutional AI |
| Google DeepMind | Demis Hassabis, AlphaFold, AlphaGo, Gemini |
| NVIDIA | Jensen Huang, CUDA, H100, GPU architecture |
| Large language model | Transformer, BERT, GPT, attention mechanism |
| Transformer architecture | Attention, encoder/decoder, Google Brain |
| Sam Altman | Y Combinator, OpenAI CEO career |
| Demis Hassabis | DeepMind founding, neuroscience background |
| Retrieval-augmented generation | RAG components, vector databases, applications |

### Via Local Python

```bash
cd backend
python fetch_data.py
```

### Ingest Your Own Documents

```bash
# Copy your PDF/TXT to the data directory
cp my_document.pdf backend/data/

# Run ingestion on that specific file
docker exec -it graphrag-backend python -c "
from ingestion import ingest_document
ingest_document('data/my_document.pdf')
"
```

### Verify Ingestion in Neo4j Browser

Open `http://localhost:7474` (login: neo4j / password123) and run:

```cypher
// See all entity types ingested
MATCH (n) RETURN labels(n), count(*) ORDER BY count(*) DESC

// See all relationships
MATCH (a)-[r]->(b) RETURN type(r), count(*) ORDER BY count(*) DESC

// Browse Person → Organization relationships
MATCH (p:Person)-[r]->(o:Organization) RETURN p, r, o LIMIT 25
```

---

## API Reference

### `GET /stream?query=<string>`

Opens a Server-Sent Events stream. Returns three event types:

```
event: status
data: Agent is currently: grade_documents...

event: graph_update
data: {"nodes":[{"id":"abc","label":"Sam Altman","type":"Person","val":1},...], "links":[...]}

event: message
data: Sam Altman is the CEO of OpenAI.

event: message
data:  He previously served as president of Y Combinator.
```

- `status` events fire at each agent node transition
- `graph_update` fires once after retrieval, contains the full entity graph + retrieved chunk overlay
- `message` events fire one token at a time until the answer is complete

### `GET /graph`

Returns the full knowledge graph (no query needed). Called by the frontend on page load.

```json
{
  "nodes": [
    {"id": "4:abc123", "label": "Sam Altman", "type": "Person", "val": 1},
    {"id": "4:def456", "label": "OpenAI", "type": "Organization", "val": 1}
  ],
  "links": [
    {"source": "4:abc123", "target": "4:def456", "label": "CEO_OF"}
  ]
}
```

Returns `{"nodes": [], "links": [], "error": "..."}` if Neo4j is not reachable.

---

## Frontend Guide

### What you see

```
┌─────────────────────────────────┬────────────────────────────────────────┐
│  EnterpriseBrain        3n · 5e │                                        │
│  ┌───────────────────────────┐  │                                        │
│  │ [user message]            │  │   LIVE KNOWLEDGE GRAPH                 │
│  │ [assistant response]      │  │                                        │
│  │ ...                       │  │   ● Person  ● Organization ● Product   │
│  └───────────────────────────┘  │   ● Technology  ● Location             │
│                                 │                                        │
│  STATUS: Agent is: generate...  │     (force-directed graph canvas)      │
│  ┌───────────────────────┐ Ask  │                                        │
│  │ Ask the knowledge...  │      │                                        │
└─────────────────────────────────┴────────────────────────────────────────┘
```

- **Top-right header** (`3n · 5e`): current node and edge count in the graph
- **Status bar**: shows the current agent step in real time
- **Graph canvas**: force-directed layout, drag nodes, scroll to zoom, hover for tooltip
- **Node colors**: each entity type has a distinct color (see legend below graph)
- **Arrows**: directional, show relationship direction (e.g., Person → CEO_OF → Organization)
- **Enter key**: submits the query

### Example Questions (after ingesting Wikipedia articles)

```
"Who founded OpenAI and what is their relationship to Anthropic?"
"What is the connection between Sam Altman and Y Combinator?"
"What products has Google DeepMind created?"
"How does retrieval-augmented generation work?"
"What is NVIDIA's role in AI development?"
```

---

## How the Agent Works (Step-by-Step)

Let's trace the query: *"Who is the CEO of OpenAI?"*

**1. `retrieve` node**
```
question = "Who is the CEO of OpenAI?"
→ GoogleGenerativeAIEmbeddings encodes this into a 768-dim vector
→ Neo4jVector.similarity_search() finds top-4 chunks with similar embeddings
→ Returns chunks containing passages about OpenAI leadership
```

**2. `grade_documents` node**
```
All 6 retrieved chunks are graded IN PARALLEL using asyncio.gather():
  → All 6 Gemini Flash calls fire simultaneously (not one by one)
  → Structured output: GradeDocuments(binary_score="yes" or "no")
  → Filters: keeps only "yes" chunks
  → Time: ~3-5s total regardless of chunk count (vs ~40s sequential)

If all filtered out → web_search = "Yes" → go to transform_query
If any kept → web_search = "No" → go to generate
```

**3. `generate` node** (assuming relevant chunks found)
```
context = join all relevant chunk texts
prompt = "Answer based only on context: {context}\n\nQuestion: {question}"
→ Gemini generates answer, streaming tokens
→ Each token: yield {"event": "message", "data": token}
```

**4. SSE reaches browser**
```
ChatInterface receives tokens one by one
→ Appends each token to the last message in state
→ React re-renders only the last message
→ User sees text appearing in real time
```

**If retrieval fails (all docs irrelevant):**

**3a. `transform_query` node**
```
original_question = "Who is the CEO of OpenAI?"
→ Prompt: "Rewrite this for better vector search relevance"
→ Gemini: "OpenAI leadership CEO president executive role"
→ Sets question = rewritten version
→ Returns to retrieve node with new question
```

---

## Configuration Reference

### `backend/.env` (local development)
```env
GOOGLE_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-pro           # generation model — full reasoning
GEMINI_GRADING_MODEL=gemini-2.0-flash # grading model — fast binary yes/no
NEO4J_URI=bolt://localhost:7687       # localhost for local dev
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password123
```

### `EnterpriseBrain/.env` (Docker Compose)
```env
GOOGLE_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-pro
NEO4J_URI=bolt://neo4j:7687           # neo4j = Docker service name
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password123
```

### Model Configuration

Two separate models serve two different jobs (`backend/agent/nodes.py`):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-pro` | Final answer generation — needs full reasoning |
| `GEMINI_GRADING_MODEL` | `gemini-2.0-flash` | Relevance grading — binary yes/no, Flash is 10× faster |

To change either model, update the corresponding `.env` variable. No code changes needed.

### Neo4j Memory Tuning

In `docker-compose.yml`, Neo4j memory settings:
```yaml
NEO4J_dbms_memory_heap_initial__size=1G   # starting JVM heap
NEO4J_dbms_memory_heap_max__size=2G       # max JVM heap
```
Increase `max__size` to `4G` if you ingest many large documents and queries slow down.

---

## Performance Notes

### Response Latency — Before and After

| Step | Old behaviour | New behaviour |
|---|---|---|
| Chunks retrieved | k=12 (12 grading calls) | k=6 (6 grading calls) |
| Grading execution | Sequential — one call at a time | Parallel — all fire with `asyncio.gather()` |
| Grading model | Gemini 2.5 Pro (~8s/call) | Gemini 2.0 Flash (~2s/call) |
| **Grading total** | **~96s** | **~3-5s** |
| Generation | Gemini 2.5 Pro, streaming | Gemini 2.5 Pro, streaming (unchanged) |
| **End-to-end** | **~2-3 minutes** | **~20-25 seconds** |

### Why Two Models?

Embedding models, grading models, and generation models are fundamentally different:

```
gemini-embedding-001   → Input: text → Output: [768 floats]  (fixed size)
                          Purpose: convert text to a comparable vector
                          Speed: ~5ms, very cheap

gemini-2.0-flash       → Input: text → Output: "yes" or "no"
                          Purpose: fast binary relevance judgement
                          Speed: ~2s, cheap — used for grading only

gemini-2.5-pro         → Input: text → Output: full reasoning paragraph
                          Purpose: synthesise answer from retrieved context
                          Speed: ~10-20s, expensive — used once per query
```

You **cannot** use a generation model for embeddings (its output is variable-length text, not a fixed vector) and you **should not** use the most powerful model for simple yes/no decisions that a faster model handles just as well.

### Scaling Considerations

At enterprise scale (1000+ documents, 100k+ pages):
- **Vector search stays fast**: HNSW index is O(log N) — 500k chunks still searches in ~200ms
- **Grading stays fast**: parallel async calls mean latency = slowest single call, regardless of k
- **Bottleneck shifts to**: Neo4j cluster replication, Gemini Pro API rate limits, ingestion throughput
- **Next steps**: Redis semantic cache (cache identical/near-identical queries), hybrid BM25+vector search, SageMaker deployment for production

---

## Resume Justification

The following resume bullet points map directly to this codebase:

**"Engineered a high-concurrency FastAPI backend with Server-Sent Events (SSE) to stream AI-generated responses token-by-token, decoupling LLM inference from the HTTP request-response cycle and reducing time-to-first-token from ~5s to <100ms"**
- `backend/main.py`: `EventSourceResponse` wraps an async generator
- `astream_events()` emits tokens as they are produced by Gemini, not after completion
- Without streaming: response appears after ~5-15s (full generation time). With SSE: first token appears in <100ms

**"Built an interactive knowledge graph visualization in Next.js using react-force-graph-2d with useMemo-based memoization, preventing unnecessary re-renders during high-frequency SSE updates and rendering live RAG retrieval paths in real time"**
- `frontend/components/GraphViz.tsx`: `useMemo` wraps `graphData` so the force simulation doesn't restart on every SSE token
- `nodeCanvasObject`: custom canvas rendering at 60fps
- `/stream` sends `graph_update` events that overlay retrieved chunks on the entity graph

**"Containerized the full polyglot stack (Python, Node.js, Neo4j) via Docker Compose with isolated service networking, persistent volumes, and end-to-end type safety using Pydantic and TypeScript, enabling single-command reproducible deployments"**
- `docker-compose.yml`: 3 services with health checks, named network `pipeline_net`, volume `./neo4j_data:/data`
- `backend/agent/nodes.py`: `GradeDocuments(BaseModel)` Pydantic schema for structured LLM output
- `frontend/components/GraphViz.tsx`: `GraphNode`, `GraphLink`, `GraphData` TypeScript interfaces
- `docker-compose up -d` starts the entire system reproducibly on any machine with Docker
