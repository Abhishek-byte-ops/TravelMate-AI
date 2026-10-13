import os
import certifi
from dotenv import load_dotenv

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where() 

from typing import TypedDict, Annotated
import operator
import uuid

import psycopg
from psycopg.rows import dict_row

from langgraph.graph import StateGraph, START,  END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import AnyMessage,HumanMessage,AIMessage,SystemMessage
from langchain_groq import ChatGroq
# from tools.tavily_tool import tavilySearch
from tools.flight_tool import search_flights
from mcp_client import aviationstack_mcp_flights
from guardrails import check_input
from pydantic import BaseModel, Field
from mcp_client_test import tavily_mcp_search
import asyncio

load_dotenv()

def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError("DATABASE URL missing")

    if "sslmode" not in database_url:
        seperator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{seperator}sslmode=require"

    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")

# LLM

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=GROQ_API_KEY
    )

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_query: str
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int
    guard_ok: bool
    guard_category: str
    # Written by the supervisor, read by dispatch()
    search_query: str
    needs_flights: bool
    needs_hotels: bool
    needs_itinerary: bool
    plan_reason: str
    # Per-turn progress. Deliberately NOT an accumulating reducer: with
    # operator.add it carried over between turns and dispatch() skipped
    # every specialist on follow-ups.
    completed: list[str]
    # Human-in-the-loop review of the itinerary
    review_status: str  # pending | approved | revise | skipped
    review_feedback: str
    revision_round: int


def guard_agent(state: TravelState):
    """
    First node in the graph. Validates the request before any paid call.

    A thread with prior messages is treated as a follow-up, which relaxes
    the relevance rule - "make it 5 days" has no travel words but is valid
    in context. On refusal the message is appended as the assistant turn so
    run_travel_agent returns it like any other reply, and the conditional
    edge routes straight to END.
    """
    history = state.get("messages", [])
    is_followup = len(history) > 1

    verdict = check_input(state.get("user_query", ""), is_followup=is_followup)

    if verdict.allowed:
        return {"guard_ok": True, "guard_category": verdict.category}

    return {
        "guard_ok": False,
        "guard_category": verdict.category,
        "messages": [AIMessage(content=verdict.message)],
    }


def route_after_guard(state: TravelState):
    """Conditional edge: run the pipeline only for accepted requests."""
    return "supervisor_agent" if state.get("guard_ok") else END


class RoutingPlan(BaseModel):
    """What the supervisor decides for one request."""

    needs_flights: bool = Field(
        description="True if the user wants flight or airline information."
    )
    needs_hotels: bool = Field(
        description="True if the user wants hotels, stays or accommodation."
    )
    needs_itinerary: bool = Field(
        description="True if the user wants a day-by-day plan or schedule."
    )
    search_query: str = Field(
        description=(
            "A standalone travel request combining this message with the "
            "conversation so far. Must name the origin and destination "
            "explicitly, e.g. '5 day trip from Delhi to Kathmandu'."
        )
    )
    reason: str = Field(description="One short sentence explaining the choice.")


def supervisor_agent(state: TravelState):
    """
    Decides which agents to run, instead of a hard-coded chain.

    One LLM call, not one per step: the Groq free tier allows 8,000 tokens
    per minute across the whole request, so a re-planning loop would spend
    the budget the agents need. dispatch() then walks the plan with no
    further model calls.

    It also rewrites the request into a standalone search_query. A bare
    follow-up like "make it 7 days" resolves to nothing on its own, which
    previously sent flight_agent to the unfiltered /v1/flights endpoint and
    returned random worldwide flights.
    """
    asked = [
        m.content
        for m in state.get("messages", [])[:-1]
        if isinstance(m, HumanMessage)
    ][-3:]

    context = "\n".join(f"- {q}" for q in asked) or "- (none, this is the first turn)"

    prompt = f"""Earlier requests in this conversation:
{context}

Current request: "{state['user_query']}"

Decide which specialists are needed and restate the request so it stands
alone. Only ask for what the user actually wants: a flights-only question
needs no hotels, and a hotel question needs no itinerary."""

    plan = llm.with_structured_output(RoutingPlan).invoke(
        [
            SystemMessage(
                content=(
                    "You route travel requests to specialist agents. Be "
                    "conservative: select only the specialists the request "
                    "actually needs."
                )
            ),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "search_query": plan.search_query or state["user_query"],
        "needs_flights": plan.needs_flights,
        "needs_hotels": plan.needs_hotels,
        "needs_itinerary": plan.needs_itinerary,
        "plan_reason": plan.reason,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def dispatch(state: TravelState):
    """
    Conditional edge shared by the supervisor and every specialist.

    Deterministic, so walking the plan costs no tokens: it returns the
    first selected agent that has not reported itself in `completed`,
    and falls through to final_agent when the plan is exhausted.
    """
    done = set(state.get("completed", []))

    if state.get("needs_flights") and "flight_agent" not in done:
        return "flight_agent"

    if state.get("needs_hotels") and "hotel_agent" not in done:
        return "hotel_agent"

    if state.get("needs_itinerary") and "itinerary_agent" not in done:
        return "itinerary_agent"

    # Human-in-the-loop: an itinerary is reviewed before the write-up.
    # review_status is reset to "pending" on each revision, so this gate
    # re-arms for the next round rather than being a one-shot.
    if state.get("itinerary") and state.get("review_status") == "pending":
        return "review_agent"

    return "final_agent"


def flight_agent(state: TravelState):
    # search_query is the supervisor's standalone rewrite; user_query is
    # the raw message, which may be a contextless follow-up.
    query = state.get("search_query") or state["user_query"]
    flight_data = asyncio.run(aviationstack_mcp_flights(query))

    return {
        "flight_results": flight_data,
        "completed": state.get("completed", []) + ["flight_agent"],
    }


def hotel_agent(state: TravelState):
    resolved = state.get("search_query") or state["user_query"]
    query = f"Best Hotels for {resolved}"
    hotel_results = asyncio.run(tavily_mcp_search(query))

    return {
        "hotel_results": hotel_results,
        "completed": state.get("completed", []) + ["hotel_agent"],
    }

# Raw tool output is verbose - Tavily returns whole page extracts - and the
# Groq free tier allows 8,000 tokens per minute across both LLM calls in one
# request. These caps keep the prompts inside that budget; the itinerary
# already distils the full tool output, so the tail adds little.
FLIGHT_CHARS = 1600
HOTEL_CHARS = 1200


def clip(text, limit):
    """Trim tool output to a character budget, noting what was dropped."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + f"\n... [trimmed {len(text) - limit} chars]"


def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state.get('search_query') or state['user_query']}

Flight Results:
{clip(state.get('flight_results'), FLIGHT_CHARS)}

Hotel Results:
{clip(state.get('hotel_results'), HOTEL_CHARS)}


Make the itinerary practical, budget-aware, and easy to follow.
"""

    # On a revision round, tell the planner what the user objected to.
    feedback = state.get("review_feedback", "")
    if feedback:
        prompt += f"""

The user reviewed the previous itinerary and asked for these changes:
{feedback}

Rewrite the itinerary to address that feedback."""

    response = llm.invoke(
        [
            SystemMessage(content="You are an expert travel planner."),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "itinerary": response.content,
        "completed": state.get("completed", []) + ["itinerary_agent"],
        # Re-arm the review gate so the rewritten plan is shown too.
        "review_status": "pending",
        "llm_calls": state.get("llm_calls", 0) + 1,
    }

# Earlier user requests to replay. Each one costs ~10 tokens, while a full
# assistant plan costs ~1,900 - and the Groq free tier allows only 8,000
# tokens per minute across both LLM calls in a request. So history carries
# the questions, not the answers: enough to resolve "make it 5 days instead"
# against the trip already discussed, at negligible token cost.
HISTORY_TURNS = 4


def conversation_history(state):
    """
    Earlier user turns of this thread, restored from Postgres by the
    checkpointer into state["messages"].

    The last entry is the current question, which final_prompt already
    covers, so it is dropped to avoid asking twice.
    """
    messages = state.get("messages", [])[:-1]
    earlier = [m for m in messages if isinstance(m, HumanMessage)]
    return earlier[-HISTORY_TURNS:]


# How many times the user may send the itinerary back before the graph
# stops asking and writes up whatever it has.
MAX_REVISIONS = 2


def review_agent(state: TravelState):
    """
    Human-in-the-loop gate. Pauses the graph so the user can read the
    itinerary before the final answer is written.

    interrupt() suspends execution here and the checkpointer persists
    everything to Postgres, so the pause survives a server restart - the
    run is resumed later with Command(resume=...), not held in memory.

    On the first pass interrupt() does not return: LangGraph stops the run
    and the payload below reaches the caller as result["__interrupt__"].
    When the thread is resumed, the same call returns the value the user
    supplied.
    """
    decision = interrupt(
        {
            "type": "itinerary_review",
            "itinerary": state.get("itinerary", ""),
            "revision_round": state.get("revision_round", 0),
            "question": "Approve this itinerary, or tell me what to change.",
        }
    )

    # decision comes from Command(resume=...) - see resume_travel_agent
    action = (decision or {}).get("action", "approve")
    feedback = (decision or {}).get("feedback", "")

    if action == "revise":
        return {
            "review_status": "revise",
            "review_feedback": feedback,
            "revision_round": state.get("revision_round", 0) + 1,
        }

    return {"review_status": "approved", "review_feedback": ""}


def route_after_review(state: TravelState):
    """
    Send the itinerary back for another pass, or move on.

    Revisions are capped: without MAX_REVISIONS a user could bounce the
    plan indefinitely, and each round costs an LLM call.
    """
    if (
        state.get("review_status") == "revise"
        and state.get("revision_round", 0) <= MAX_REVISIONS
    ):
        return "itinerary_agent"

    return "final_agent"


def final_agent(state: TravelState):
    # Only describe the data that was actually gathered. The supervisor may
    # have skipped specialists, and asking for a "Flight Information"
    # section with no flight data just produces an empty heading.
    sections = ["Trip Summary"]
    blocks = [f"User Request:\n{state.get('search_query') or state['user_query']}"]

    if state.get("flight_results"):
        sections.append("Flight Information")
        blocks.append(f"Flights:\n{clip(state['flight_results'], FLIGHT_CHARS)}")

    if state.get("hotel_results"):
        sections.append("Hotel Suggestions")
        blocks.append(f"Hotels:\n{clip(state['hotel_results'], HOTEL_CHARS)}")

    if state.get("itinerary"):
        sections.append("Day-by-day Itinerary")
        blocks.append(f"Itinerary:\n{state['itinerary']}")

    sections += ["Estimated Budget", "Final Recommendations"]

    numbered = "\n".join(f"    {i}. {name}" for i, name in enumerate(sections, 1))
    gathered = "\n\n    ".join(blocks)

    final_prompt = f"""
    Generate the final travel response for the user.

    {gathered}

    Format the final answer using exactly these sections:

{numbered}

    Important:
    - Be clear and practical
    - Label budget figures as rough estimates from general knowledge, not
      live prices, and tell the user to verify on a booking site
    - Mention that the live flight API does not provide ticket prices
      whenever flight information is included
    - Keep the response useful for real travel planning
   """
    history = conversation_history(state)

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are a professional AI travel booking assistant. "
                    "The user's earlier requests in this conversation follow. "
                    "Use them to resolve follow-ups like 'make it 5 days "
                    "instead' against the trip already discussed."
                )
            ),
            *history,
            HumanMessage(content=final_prompt),
        ]
    )

    return{
        "messages":[response],
        "llm_calls": state.get("llm_calls",0)+1
    }


# Build graph

graph = StateGraph(TravelState)

graph.add_node("guard_agent", guard_agent)
graph.add_node("supervisor_agent", supervisor_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("review_agent", review_agent)
graph.add_node("final_agent", final_agent)

# Where dispatch() is allowed to send work. Every specialist returns here,
# so the route is decided per request rather than fixed in the graph.
DISPATCH_TARGETS = {
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "itinerary_agent": "itinerary_agent",
    "review_agent": "review_agent",
    "final_agent": "final_agent",
}

graph.add_edge(START, "guard_agent")

# Rejected requests skip the pipeline entirely.
graph.add_conditional_edges(
    "guard_agent",
    route_after_guard,
    {"supervisor_agent": "supervisor_agent", END: END},
)

graph.add_conditional_edges("supervisor_agent", dispatch, DISPATCH_TARGETS)
graph.add_conditional_edges("flight_agent", dispatch, DISPATCH_TARGETS)
graph.add_conditional_edges("hotel_agent", dispatch, DISPATCH_TARGETS)
graph.add_conditional_edges("itinerary_agent", dispatch, DISPATCH_TARGETS)

# After the human decides: another planning round, or the write-up.
graph.add_conditional_edges(
    "review_agent",
    route_after_review,
    {"itinerary_agent": "itinerary_agent", "final_agent": "final_agent"},
)

graph.add_edge("final_agent", END)


DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit= True,
    row_factory=dict_row
)

checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


def run_travel_agent(user_input:str , thread_id:str | None =None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable":{
            "thread_id": thread_id
        }
    }

    result = travel_graph.invoke(
        {
            "messages":[
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "flight_results":"",
            "hotel_results":"",
            "itinerary":"",
            "llm_calls":0,
            "guard_ok": False,
            "guard_category": "",
            "search_query": "",
            "needs_flights": False,
            "needs_hotels": False,
            "needs_itinerary": False,
            "plan_reason": "",
            "completed": [],
            "review_status": "pending",
            "review_feedback": "",
            "revision_round": 0,

        },
        config=config
    )

    return _result_payload(result, thread_id)


def resume_travel_agent(thread_id, action, feedback=""):
    """
    Resume a thread paused at review_agent.

    action: "approve" to continue to the write-up, "revise" to send the
    itinerary back to the planner with feedback.

    The paused run lives in Postgres, not in this process, so any worker
    can resume any thread - and a restart between pause and resume is
    harmless.
    """
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = travel_graph.get_state(config)
    if not snapshot.next:
        return {
            "status": "error",
            "reply": "That conversation is not waiting for a review.",
            "thread_id": thread_id,
        }

    result = travel_graph.invoke(
        Command(resume={"action": action, "feedback": feedback}),
        config=config,
    )

    return _result_payload(result, thread_id)


def _result_payload(result, thread_id):
    """
    Shape one graph result for the API.

    A run that hit interrupt() carries "__interrupt__" and has produced no
    final message yet, so it is reported separately from a finished run.
    """
    interrupts = result.get("__interrupt__")

    if interrupts:
        payload = interrupts[0].value
        return {
            "status": "awaiting_review",
            "thread_id": thread_id,
            "itinerary": payload.get("itinerary", ""),
            "question": payload.get("question", ""),
            "revision_round": payload.get("revision_round", 0),
        }

    messages = result.get("messages", [])

    if not messages:
        return {
            "status": "error",
            "reply": "No response generated. Please try again.",
            "thread_id": thread_id,
        }

    return {
        "status": "complete",
        "reply": messages[-1].content,
        "thread_id": thread_id,
    }
