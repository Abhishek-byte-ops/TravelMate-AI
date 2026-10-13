import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Literal

from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent

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
    # "complete" carries reply; "awaiting_review" carries itinerary and
    # question instead, because the graph paused at review_agent.
    status: str
    thread_id: str
    reply: str | None = None
    itinerary: str | None = None
    question: str | None = None
    revision_round: int | None = None


class ReviewRequest(BaseModel):
    thread_id: str
    action: Literal["approve", "revise"]
    feedback: str = Field("", max_length=2000)


def asset_version():
    """
    Cache-busting token derived from the static files' mtimes.

    Without it a browser keeps serving a cached script.js against a changed
    API - which is how a stale bundle met the new awaiting_review response
    and called marked.parse(null).
    """
    stamps = []
    for name in ("static/style.css", "static/script.js"):
        try:
            stamps.append(int(os.path.getmtime(name)))
        except OSError:
            stamps.append(0)
    return str(max(stamps))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"v": asset_version()}
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Deliberately a sync def: run_travel_agent blocks for ~20-70s across the
# graph's nodes, so FastAPI runs this in its threadpool instead of stalling
# the event loop.
@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    thread_id = payload.thread_id or f"user_{uuid.uuid4().hex}"

    try:
        result = run_travel_agent(payload.message, thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}")

    return ChatResponse(**result)


# Resumes a thread paused at the human-in-the-loop review. The paused run
# lives in Postgres, so this can be a separate request - even a separate
# process - from the one that started it.
@app.post("/api/review", response_model=ChatResponse)
def review(payload: ReviewRequest):
    try:
        result = resume_travel_agent(
            payload.thread_id, payload.action, payload.feedback
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Resume failed: {exc}")

    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result["reply"])

    return ChatResponse(**result)
