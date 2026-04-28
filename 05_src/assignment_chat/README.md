# Cosmos — AI Science Companion

![Cosmos logo](../logo/Logo.jpg)

**Cosmos** is a calm, curious conversational AI astronomer built for **Assignment 2** of the *Deploying AI 3* course at the **University of Toronto Data Sciences Institute**.  Ask it about today's NASA astronomy picture, fact-check a popular science claim, or hand it a maths problem — it will answer with quiet precision.

---

## Architecture

```text
assignment_chat/
├── app.py                  # Gradio Blocks UI + LangGraph agent entry point
├── prompts.py              # Cosmos system prompt and personality
├── memory.py               # Conversation history trimming utility
├── services/
│   ├── api_service.py      # Service 1 – NASA APOD (external API)
│   ├── semantic_service.py # Service 2 – Science misconceptions (Chroma DB)
│   └── math_service.py     # Service 3 – Math tool (numexpr via LangChain)
└── data/
    └── chroma_db/          # Persistent vector store (created at runtime)
```

### LangGraph agent flow

```text
gr.Blocks  (custom HTML header with project logo + UofT crest)
  └─ gr.ChatInterface
        │  message + history
        ▼
  guardrail_check()          ← blocks banned topics & prompt-injection probes
        │
        ▼
  LangGraph MessagesState
        │
        ▼
  _call_model  (LLM node)    ← GPT-4o-mini + 3 bound tools
        │
        ├─── tool call? ──► ToolNode (routes to one of the 3 services)
        │                        │
        │◄───────────────────────┘  (tool result added to state)
        │
        ▼
  final AIMessage.content    ← returned to Gradio
```

The UI is built with `gr.Blocks`, which wraps a custom HTML banner (project logo base64-embedded, UofT coat of arms from Wikimedia) above a standard `gr.ChatInterface`.  The `ChatInterface` manages the conversation history automatically.
Each turn the full history is converted to LangChain `HumanMessage` / `AIMessage`
objects and passed to LangGraph as `MessagesState`.  A sliding window trim
(default: 20 messages ≈ 10 exchanges) prevents context-window overflow on long
sessions.

---

## Services

### Service 1 — NASA Astronomy Picture of the Day (`api_service.py`)

| Detail | Value |
| --- | --- |
| API | [NASA APOD](https://api.nasa.gov/) |
| Endpoint | `GET https://api.nasa.gov/planetary/apod` |
| Auth | `NASA_API_KEY` (see below) or public `DEMO_KEY` |
| Output transform | JSON → condensed natural-language summary with Markdown link |

The raw API response (JSON with `title`, `explanation`, `url`, …) is **not** returned verbatim.  Instead the tool extracts the title, date, and a condensed explanation (≤ 700 characters) and formats them as readable prose.

### Service 2 — Science Misconceptions Semantic Search (`semantic_service.py`)

| Detail | Value |
| --- | --- |
| Dataset | *List of common misconceptions about science* (Wikipedia-style HTML, already in `05_src/documents/`) |
| Embedding model | `text-embedding-3-small` via OpenAI / course gateway |
| Vector DB | ChromaDB with file persistence (`data/chroma_db/`) |
| Chunk size | 500 tokens, 60-token overlap |
| Retrieval | Top-3 nearest neighbours (cosine similarity) |

**Embedding process:**

1. On first launch, `ingest_documents()` is called automatically.
2. The HTML file is stripped of tags with a regex-based extractor.
3. The plain text is split with `RecursiveCharacterTextSplitter`.
4. Chunks are embedded in batches of 100 and stored in the persistent Chroma collection `science_misconceptions`.
5. On subsequent launches the stored collection is loaded directly — no re-embedding occurs.

> **Note:** The `data/chroma_db/` directory is excluded from version control.  The first run will embed the source document (requires an active API key and internet connection).

### Service 3 — Math Tool (`math_service.py`)

Wraps `math_tools.get_math_tool()` from `05_src/math_tools.py`.
The tool uses GPT-4o-mini with structured output to translate a natural-language
maths problem into a `numexpr` expression, evaluates it, and returns the result.
Supports arithmetic, word problems, and simple algebraic expressions.

---

## Guardrails

The following topics are **always refused**, regardless of phrasing:

| Category | Examples |
| --- | --- |
| Cats or dogs | cat, kitten, dog, puppy, canine, feline |
| Horoscopes / Zodiac | horoscope, zodiac, astrology, aries, taurus, … |
| Taylor Swift | "taylor swift" |

In addition, any attempt to reveal or override the system prompt is detected and
politely deflected.

---

## Setup

### 1 — Copy and fill in secrets

```bash
cp 05_src/.secrets.template 05_src/assignment_chat/.secrets
```

Edit `05_src/assignment_chat/.secrets` and fill in:

```dotenv
API_GATEWAY_KEY=<your course API gateway key>
OPENAI_API_KEY=any_value          # set to a real key if not using the gateway
NASA_API_KEY=<optional NASA key>  # omit to use the free DEMO_KEY (30 req/hr)
```

> **OPENAI_BASE_URL** defaults to the course gateway
> (`https://k7uffyg03f.execute-api.us-east-1.amazonaws.com/prod/openai/v1`).
> Override it by adding `OPENAI_BASE_URL=…` to `.secrets` if needed.

### 2 — Verify the environment

All required libraries are part of the standard course environment (`pyproject.toml`):
`langchain`, `langchain-openai`, `langgraph`, `chromadb`, `openai`, `gradio`,
`python-dotenv`, `requests`, `langchain-text-splitters`, `numexpr`.

No additional packages need to be installed.

### 3 — Run the app

```bash
cd 05_src/assignment_chat
python app.py
```

On first launch the science-facts database is ingested automatically (may take
~30 seconds depending on API latency).  Subsequent launches are instant.

Open the URL printed by Gradio (default `http://127.0.0.1:7860`) in your browser.

---

## Usage examples

| Input | Service called |
| --- | --- |
| "What is the astronomy picture of the day?" | NASA APOD |
| "Show me the APOD from 2024-12-25" | NASA APOD |
| "Is it true that we only use 10% of our brain?" | Science facts search |
| "Does lightning ever strike the same place twice?" | Science facts search |
| "How many seconds are in a leap year?" | Math tool |
| "If the speed of light is 299,792 km/s, how far does it travel in 8 minutes?" | Math tool |

---

## Limitations & future improvements

- **Science-facts coverage:** The dataset is a single Wikipedia article (~500 chunks).  Expanding to a curated multi-source corpus would improve recall.
- **DEMO_KEY rate limit:** The NASA API's free key allows 30 requests/hour and 50/day.  Adding `NASA_API_KEY` removes this constraint.
- **Guardrail brittleness:** The keyword filter can produce false positives (e.g. "cancer" the disease vs. the zodiac sign).  A lightweight classifier would be more robust.
- **Memory:** History is trimmed to the last 20 messages.  A summarisation step (using the LLM) could better compress long conversations while preserving key context.
- **Tool parallelism:** LangGraph currently runs tools sequentially; switching to a parallel tool-call pattern would reduce latency for multi-step queries.
