# probes: json.dumps accepts the separators keyword
# expect:
# [1,2]
import json

print(json.dumps([1, 2], separators=(",", ":")))
