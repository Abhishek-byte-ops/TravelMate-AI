"""
Local MCP server exposing this project's own flight search.

The public aviationstack-mcp server only offers tools this Aviationstack
plan blocks (list_routes, historical_flights_by_date, list_airports), and
its one usable tool - the airport schedule board - carries no arrival
airport, so it cannot answer route queries. tools.flight_tool.search_flights
calls /v1/flights directly, which the plan does allow and which supports
both dep_iata and arr_iata, so wrapping it keeps the original
route-filtered behaviour while still going through MCP.

Run directly for a stdio server:
    travel/bin/python travel_mcp_server.py
"""

from mcp.server.fastmcp import FastMCP

from tools.flight_tool import search_flights as _search_flights

mcp = FastMCP("travelmate")


@mcp.tool(
    name="search_flights",
    description=(
        "Find live flights for a natural-language travel request. Resolves "
        "countries, cities and IATA codes in the query to a departure and "
        "arrival airport, then returns matching live flights with airline, "
        "flight number, status, terminals, gates, scheduled times and delays. "
        "Handles 'from X to Y', 'to Y from X', 'flights from X' and bare "
        "IATA pairs such as 'DEL to KTM'."
    ),
)
def search_flights(query: str, limit: int = 10) -> str:
    """Return formatted live flight information for a travel query."""
    return _search_flights(query, limit=limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
