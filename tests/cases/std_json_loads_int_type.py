# probes: json.loads yields ints, not strings
# expect:
# [1, 2, 3]
# int
import json

parsed = json.loads("[1, 2, 3]")
print(parsed)
print(type(parsed[0]).__name__)
