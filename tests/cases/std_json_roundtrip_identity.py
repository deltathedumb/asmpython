# probes: dumps/loads round-trips a nested structure
# expect:
# True
import json

original = {"xs": [1, 2], "s": "t"}
print(json.loads(json.dumps(original)) == original)
