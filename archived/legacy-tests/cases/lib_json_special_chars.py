# expect:
# {"key": "a\nb", "num": 3.14}
import json
print(json.dumps({'key': 'a\nb', 'num': 3.14}))
# asmpython (beta/3.14.0) rejects at compile: [E148] mixed dict value types (str and float); a float value can't share a dict with non-floats
