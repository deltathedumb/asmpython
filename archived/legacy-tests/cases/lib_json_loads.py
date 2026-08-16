# expect:
# 1 2
import json
d = json.loads('{"x": 1, "y": 2}')
print(d['x'], d['y'])
# asmpython (beta/3.14.0) rejects at compile: [E017] cannot index a int
