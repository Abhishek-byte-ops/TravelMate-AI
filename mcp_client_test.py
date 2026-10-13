import os
import asyncio
import certifi
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


client = MultiServerMCPClient(
{
    "tavily":{
        "transport":"streamable_http",
        "url":f"https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY}",
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