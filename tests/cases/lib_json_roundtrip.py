# expect:
# [1, 2, 3]
import json
print(json.loads(json.dumps([1, 2, 3])))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
