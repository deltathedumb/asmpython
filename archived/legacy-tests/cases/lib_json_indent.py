# expect:
# {
#   "a": 1
# }
import json
print(json.dumps({'a': 1}, indent=2))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
