from tools.tavily_tool import tavilySearch
from tools.flight_tool import search_flights

from backend import run_travel_agent


# res = tavilySearch("Best hotels in india")
# print(res)


# res = search_flights("Plan 7 day Japan trip from India")
# print(res)

res = run_travel_agent("Plan me a complete 5 day rtip from India to Nepal including flights and hotels","test_user")
print(res)