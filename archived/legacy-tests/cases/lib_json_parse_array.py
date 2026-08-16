# expect:
# 4
import json
print(json.loads('[1, 2, [3, 4]]')[2][1])
# asmpython (beta/3.14.0) rejects at compile: [E017] cannot index a int
