from tools.tavily_tool import tavilySearch
from tools.flight_tool import search_flights


# res = tavilySearch("Best hotels in india")
# print(res)


res = search_flights("Plan 7 day Japan trip from India")
print(res)