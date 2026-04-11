# Project Atlas: The Enterprise Brain

This document is your **Mental Map** of the project. It is designed to help you visualize *what* we built, *where* everything lives, and *how* it all connects.

## 1. The "Why" (The Core Concept)
**Problem**: Standard AI chatbots "hallucinate" (make things up) because they just guess the next word based on probability.
**Solution**: We built a "Brain" that has two parts:
1.  **Memory (Neo4j)**: A structured database of facts (Knowledge Graph) + text search (Vectors).
2.  **Reasoning (LangGraph)**: An agent that "thinks" in steps (Plan -> Search -> Check -> Answer).

**Analogy**: Instead of a student guessing answers on a test (Standard AI), this is a student who goes to the library, looks up specific books, checks if the facts match the question, and *then* writes the answer.

---

## 2. The Architecture (The Big Picture)
This diagram shows how the pieces fit together.

```mermaid
graph TD
    User["👤 User"] -->|1. Asks Question| Frontend["💻 Frontend (Next.js)"]
    Frontend -->|2. Sends Query| Backend["⚙️ Backend (FastAPI)"]
    
    subgraph "The Agent (Brain)"
        Backend -->|3. Starts Workflow| Agent["🤖 LangGraph Agent"]
        Agent -->|4. Decides Strategy| Router{"Router"}
        Router -->|5. Vector Search| Vector["🔍 Vector Index"]
        Router -->|6. Graph Traversal| Graph["🕸️ Knowledge Graph"]
        Vector -->|7. Retrieve Facts| Context["📄 Context"]
        Graph -->|7. Retrieve Facts| Context
        Context -->|8. Grade & Generate| LLM["🧠 Gemini 2.5 Pro"]
    end
    
    subgraph "The Database (Memory)"
        Vector -.->|Stored In| Neo4j[("Neo4j Database")]
        Graph -.->|Stored In| Neo4j
    end

    LLM -->|9. Stream Answer| Frontend
    Neo4j -->|10. Stream Graph Data| Frontend
```

---

## 3. The "Life of a Query" (Step-by-Step)
Imagine a user asks: *"How is Company A connected to Company B?"*

1.  **Frontend ([ChatInterface.tsx](frontend/components/ChatInterface.tsx))**:
    *   Catches the user's text.
    *   Opens a "Stream" using Server-Sent Events (SSE) to the backend.
    *   *Visual*: Shows "Connecting to Agent..." status.

2.  **Backend ([main.py](backend/main.py))**:
    *   Receives the question.
    *   Wakes up the **Agent** and streams `astream_events` back.

3.  **The Agent's Journey ([graph.py](backend/agent/graph.py) & [nodes.py](backend/agent/nodes.py))**:
    *   **Step A: Retrieve**: The agent uses Google Embeddings (`text-embedding-004`) to search Neo4j. It pulls Semantic Entities and Vector chunks.
    *   **Step B: Grade**: The agent (secretly) asks Gemini via Structured Output Grader: *"Does this information actually answer the question?"*
        *   *If No*: It rewrites the search query (`transform_query`) and tries again (The Loop).
        *   *If Yes*: It proceeds.
    *   **Step C: Generate**: The agent writes the final answer using the filtered facts it retrieved.

4.  **The Response**:
    *   The **Text** appears word-by-word on the screen via SSE (`@microsoft/fetch-event-source`).
    *   The **Graph** ([GraphViz.tsx](frontend/components/GraphViz.tsx)) updates using `react-force-graph-2d`, drawing the actual nodes (Company A, Company B), the Query, and the specific relationship lines the agent traversed.

---

## 4. The File Map (Where things live)
Here is the physical layout of your project, with a plain-English explanation for every file.

### 📂 `backend/` (The Brain)
*   📄 **`main.py`**: **The Doorway**. This is the FastAPI server. It streams the Agent's events (`/stream`) and provides the initial Neo4j dataset (`/graph`) for the frontend visualization.
*   📄 **`ingestion.py`**: **The Librarian**. Reads PDFs/Text, splits them into chunks (Vector Index), uses `LLMGraphTransformer` to dynamically extract Entities (Graph), and manually links contiguous text chunks via Cypher (`[:NEXT_CHUNK]`).
*   📄 **`requirements.txt`**: **The Shopping List**. Lists all the Python tools we need (LangChain, FastAPI, Google GenAI SDKs, Neo4j, etc.).
*   📂 **`agent/`**: **The Thinking Logic**
    *   📄 **`graph.py`**: **The Flowchart**. Dictates the conditional edges (Start -> Retrieve -> Check -> Generate/Rewrite -> End).
    *   📄 **`nodes.py`**: **The Workers**. Contains the `Neo4jVector.as_retriever()`, the LLM grader, and query re-writer logic driven by Gemini 2.5 Pro.
    *   📄 **`state.py`**: **The Short-term Memory**. Tracks the question, retrieved documents, generations, and decision fallbacks (`web_search`).

### 📂 `frontend/` (The Face)
*   📄 **`app/page.tsx`**: **The Main Page**. Layout for the Next.js App Router.
*   📄 **`components/ChatInterface.tsx`**: **The Conversation**. Intercepts your messages, sends them via Streaming API, and decodes the Graph Payload alongside text tokens in real time.
*   📄 **`components/GraphViz.tsx`**: **The Visualizer**. A Force-Directed JSON graph instance matching Neo4j node colors (`Person`, `Organization`, `Chunk`, etc.) inside an animated canvas.

### 📂 `Root Directory`
*   📄 **`docker-compose.yml`**: **The Infrastructure**. Pre-configures a standalone container of Neo4j with APOC plugins running purely in Docker.
*   📄 **`frontend/Dockerfile`**: A multi-stage image blueprint for hosting your Next.js Chat interface.

---

## 5. Common Questions
### "Does Gemini talk directly to Neo4j?"
**No, and that's a good thing.**
1.  **The Agent (LangGraph/Python)** runs a search query against Neo4j Vector Stores.
2.  **Neo4j** returns the relevant facts (e.g., "Company A bought Company B").
3.  **The Agent** embeds these facts into a strict Prompt Template:
    > "Answer the question based only on the following context: [Company A bought Company B]..."
4.  **Gemini** reads the prompt and writes the answer.

**Why?** This grounds the LLM to context and prevents hallucinations entirely. It can only use the data provided to its prompt.

### "What if the database is empty or doesn't have the answer?"
1.  **Search**: The Agent looks in Neo4j and finds nothing.
2.  **Grade**: The Grader evaluates relevance and gives a "No".
3.  **Self-Correction**: The Agent sees `web_search == "Yes"`. It thinks: *"Maybe I searched for the wrong thing?"*
4.  **Rewrite**: Node `transform_query` asks the LLM to re-evaluate the semantic intent, changing keywords, and searches again.
5.  **Give Up (Honest)**: Eventually, without valid documents to map, the flow terminates transparently rather than making up facts.

### "Why not just use ChatGPT or vanilla Gemini?"
**The Use Case**: Imagine you are a **Bank** or a **Hospital**.
1.  **Privacy**: You cannot broadcast private financial data to public commercial endpoints willy-nilly.
2.  **Specificity**: Base models know nothing about recent, confidential M&A documents, internal Slack records, or zero-day financial filings unless fine-tuned or anchored with live context (RAG).
3.  **Liability & Auditing**: You need proof. A deterministic Graph shows *exactly* what nodes fed the answer. A standard AI model can't do that.

### "What is a 'Multi-hop' query?"
This is the superpower of this system.
*   **Vector Search (Standard AI)** is essentially fuzzy keyword search.
    *   *Query*: "Apple iPhone battery" -> *Result*: Docs about Apple, iPhone, and batteries.
*   **Multi-hop Graph Reasoning**: Following interconnected knowledge facts.
    *   *Query*: **"Who is the CEO of the company that bought GitHub?"**
        1.  **Hop 1**: Find "GitHub".
        2.  **Hop 2**: Traverse `[:ACQUIRED_BY]` link -> "Microsoft".
        3.  **Hop 3**: Traverse `[:CEO]` link -> "Satya Nadella".

**The Hybrid Edge**: Our system uses Neo4jVector for "Hop 1" lookup semantics, and `LLMGraphTransformer` extracted paths to traverse complex business logic.

---

## 6. Naive RAG vs. Enterprise Brain
This is the perfect slide for your presentation.

| Feature | Naive RAG (Standard) | Enterprise Brain (Your Project) |
| :--- | :--- | :--- |
| **Architecture** | **Linear** (Retrieve -> Generate) | **Cyclic** (Plan -> Retrieve -> Grade -> Loop) |
| **Retrieval** | **Vector Only** (Keywords/Similarity) | **Hybrid** (Vector + Graph Structure) |
| **Multi-Hop Reasoning** | ❌ **Fails**. Can't connect A->B->C seamlessly if split across docs. | ✅ **Excels**. Follows the visual graph edges A->B->C. |
| **Hallucination** | **High**. Guesses when unsure. | **Low**. Reflexion forces it to say "I don't know." |
| **Explainability** | **Black Box**. "Here is the answer." | **Visual Transparency**. Shown animated via React Force-Graph. |

---

## 7. Resume Snippet (Copy-Paste this)
Here is the updated version aligned specifically to your code: Gemini, LLMGraphTransformer, LangGraph, and Next.js SSE.

```latex
\cventry
  {Independent Developer} % Role
  {Autonomous Graph-Grounded Agentic RAG System ("Enterprise Brain")} % Project Name
  {} % Location
  {Jan. 2026 - Present} % Dates
  {
    \begin{cvitems}
      \item {Architected a \textbf{Neuro-Symbolic AI} framework minimizing financial hallucinations by anchoring \textbf{Gemini 2.5 Pro} reasoning to a deterministic \textbf{Neo4j Knowledge Graph}, extracted natively via Langchain's \textbf{LLMGraphTransformer}.}
      \item {Engineered a self-correcting 'Control Flow' loop using \textbf{LangGraph} that implements \textbf{Hybrid Retrieval}—combining Google \textbf{text-embedding-004} vector similarity with graph traversal to dynamically grade, reject, or rewrite queries.}
      \item {Developed a highly-responsive \textbf{FastAPI} streaming backend utilizing \textbf{Server-Sent Events (SSE)} to parse 'Thought Process' telemetry to a \textbf{Next.js} dashboard with real-time Network Visualization using \textbf{react-force-graph-2d}.}
    \end{cvitems}
  }
```

---

## 8. Real Data Strategy (Kaggle / SEC)
You want **Real Data** to make this impressive. The ingestion script (`backend/ingestion.py`) handles raw text easily:

### The "Wall Street" Approach
Download **SEC 10-K Filings** (Annual Reports) for big companies.
1.  Go to SEC.gov and download the PDF for Apple or Microsoft.
2.  Put it in `backend/data/`.
3.  Run `python ingestion.py`.
*Why?* If your AI can answer questions and visually traverse a 100-page Apple report, it proves Enterprise viability.

## 9. The Tech Stack (Complete List)

### 🧠 AI & Logic
*   **LangGraph**: Core state machine and cyclic router.
*   **LangChain**: Tooling interconnects and graph transformations (`LLMGraphTransformer`).
*   **Google Gemini 2.5 Pro**: The primary intelligence model.
*   **Google Text-Embedding-004**: The vector spatial embedder.

### ⚙️ Backend (Python)
*   **FastAPI**: API endpoints (`/graph`, `/stream`).
*   **SSE (Server-Sent Events)**: Utilizing `sse_starlette` for multi-stage payload streaming.
*   **Neo4j Python Driver**: Underlying direct Cypher query executions.

### 💾 Database
*   **Neo4j Graph Database**: Native knowledge and Vector Store persistence (Docker).

### 💻 Frontend (TypeScript)
*   **Next.js (App Router)**: Orchestration and rendering engine.
*   **Tailwind CSS**: Rapid utility styling.
*   **React Force-Graph**: 2D force-directed canvas.
*   **Microsoft Fetch Event Source**: Reliable streaming connection logic.

## 10. What do I do next?
1.  **Feed the Brain**: Put text/PDF files in `backend/data/` and run `python ingestion.py` to watch Gemini extract Entities into Neo4j.
2.  **Spin Up**: Run `docker-compose up` and `npm run dev`.
3.  **Ask Questions / Watch it Think**: Type in ChatInterface and watch the `GraphViz` dynamically draw the nodes retrieved from the graph in real-time.
