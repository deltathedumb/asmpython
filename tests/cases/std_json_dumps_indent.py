# probes: json.dumps accepts the indent keyword
# expect:
# {
#   "a": 1
# }
import json

print(json.dumps({"a": 1}, indent=2))
