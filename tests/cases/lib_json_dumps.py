# expect:
# {"a": 1, "b": [2, 3]}
import json
print(json.dumps({'a': 1, 'b': [2, 3]}))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
