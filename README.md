# TravelMate-AI

A multi-agent travel assistant built with [LangGraph](https://langchain-ai.github.io/langgraph/), using Groq for inference, Tavily for web search, and Postgres for conversation checkpointing.

## Prerequisites

- **Python 3.11**
- **PostgreSQL** — required by the LangGraph checkpointer (`langgraph-checkpoint-postgres`) to persist agent state between turns
- **API keys** — [Groq](https://console.groq.com/keys) and [Tavily](https://app.tavily.com/)
- **[uv](https://docs.astral.sh/uv/)** (recommended) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

## How to run

### 1. Create virtual env

With `uv` (downloads Python 3.11 automatically if it isn't installed):

```bash
uv venv travel --python 3.11
```

Or with the stdlib `venv`, if you already have Python 3.11 on your PATH:

```bash
python3.11 -m venv travel
```

### 2. Activate it

```bash
# macOS / Linux
source travel/bin/activate

# Windows (PowerShell)
travel\Scripts\Activate.ps1
```

Confirm you're on the right interpreter:

```bash
python --version   # -> Python 3.11.x
```

### 3. Install dependencies

```bash
uv pip install -r requirements.txt
```

Or with plain pip: `pip install -r requirements.txt`

### 4. Configure environment variables

Create a `.env` file in the project root:

```bash
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key
AVIATIONSTACK_API_KEY=your_aviationstack_api_key
DATABASE_URL=postgresql://user:password@localhost:5432/travelmate
```

`.env` is gitignored — never commit your keys.

### 5. Start Postgres

Any reachable Postgres instance works. A quick local one via Docker:

```bash
docker run --name travelmate-db \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=travelmate \
  -p 5432:5432 -d postgres:16
```

The checkpointer creates its own tables on first run via `PostgresSaver.setup()`.

### 6. Run the app

```bash
uvicorn app:app --reload --port 8000
```

Then open http://localhost:8000

## Deactivating

```bash
deactivate
```
