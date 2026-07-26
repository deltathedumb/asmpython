# expect:
# 2
import json
data = {'users': [{'id': 1}, {'id': 2}]}
s = json.dumps(data)
back = json.loads(s)
print(back['users'][1]['id'])
# asmpython (beta/3.14.0) rejects at compile: [E017] cannot index a int
