import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent

app = FastAPI(
    title="TravelMate AI",
    description="Multi-agent travel planner built on LangGraph",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    # Omit to start a new conversation; pass the value back to continue one.
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Deliberately a sync def: run_travel_agent blocks for ~20-70s across four
# graph nodes (two external API calls, two LLM calls), so FastAPI runs this in
# its threadpool instead of stalling the event loop.
@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    thread_id = payload.thread_id or f"user_{uuid.uuid4().hex}"

    try:
        reply = run_travel_agent(payload.message, thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}")

    return ChatResponse(reply=reply, thread_id=thread_id)
