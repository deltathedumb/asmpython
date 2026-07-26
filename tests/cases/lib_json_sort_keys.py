# expect:
# {"a": 1, "b": 2}
import json
print(json.dumps({'b': 2, 'a': 1}, sort_keys=True))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
