# probes: urlencode builds a query string
# expect:
# a=1&b=x+y
from urllib.parse import urlencode

print(urlencode({"a": 1, "b": "x y"}))
