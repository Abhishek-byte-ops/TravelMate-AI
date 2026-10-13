import os
import json
import asyncio
import certifi
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AVIATION_API_KEY = os.getenv("AVIATIONSTACK_API_KEY")


client = MultiServerMCPClient(
{
    "tavily":{
        "transport":"streamable_http",
        "url":f"https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY}",
    },
     "travelmate": {
         "transport": "stdio",
         "command": os.path.join(os.path.dirname(os.path.abspath(__file__)), "travel", "bin", "python"),
         "args": [
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "travel_mcp_server.py")
         ],
     },
     "aviationstack": {
         "transport": "stdio",
      "command": "uvx",
      "args": [
        "aviationstack-mcp"
      ],
      "env": {
        "AVIATION_STACK_API_KEY": AVIATION_API_KEY
      }
    }
}
)

async def get_all_tools():
    tools = await client.get_tools()
    for tool in tools:
        print(f"Tool: {tool.name}, Description: {tool.description}")


async def get_tavily_search_tool():
    # The client only exposes get_tools(); pick the one we want by name.
    tools = await client.get_tools()

    for tool in tools:
        if tool.name == "tavily_search":
            return tool

    available = ", ".join(t.name for t in tools)
    raise ValueError(f"tavily_search not found. Available tools: {available}")


async def tavily_mcp_search(query):
    tool = await get_tavily_search_tool()
    result = await tool.ainvoke({"query": query})
    print(f"Search results for '{query}':\n{result}")
    return result

async def get_aviationstack_tool(name):
    tools = await client.get_tools(server_name="aviationstack")

    for tool in tools:
        if tool.name == name:
            return tool

    available = ", ".join(t.name for t in tools)
    raise ValueError(f"{name} not found. Available tools: {available}")


async def aviationstack_mcp_flights(query, limit=10):
    """
    Flight lookup through the local travelmate MCP server.

    Goes through MCP but keeps the original route-filtered behaviour: the
    server wraps tools.flight_tool.search_flights, which calls /v1/flights
    with both dep_iata and arr_iata. The public aviationstack-mcp server
    cannot do this - list_routes and historical_flights_by_date are blocked
    on this plan, and its schedule board has no arrival airport.
    """
    tools = await client.get_tools(server_name="travelmate")

    for tool in tools:
        if tool.name == "search_flights":
            result = await tool.ainvoke({"query": query, "limit": limit})
            return mcp_text(result)

    available = ", ".join(t.name for t in tools)
    raise ValueError(f"search_flights not found. Available tools: {available}")


def mcp_text(result):
    """
    Flatten an MCP tool result to text, surfacing server-side errors.

    MCP returns tool failures as ordinary result payloads, not exceptions,
    so an unchecked result can hand a rate-limit message to the LLM as if
    it were flight data.
    """
    if isinstance(result, list):
        text = "\n".join(
            part.get("text", "") for part in result if isinstance(part, dict)
        )
    else:
        text = str(result)

    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return text

    if isinstance(payload, dict) and payload.get("ok") is False:
        return f"Flight data unavailable: {payload.get('error', 'unknown error')}"

    return text
