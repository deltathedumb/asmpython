# probes: json.dumps accepts the sort_keys keyword
# expect:
# {"a": 2, "b": 1}
import json

print(json.dumps({"b": 1, "a": 2}, sort_keys=True))
