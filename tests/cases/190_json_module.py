# expect:
# alice
# 30

import json

s: str = '{"name": "alice", "age": 30}'

parsed: dict[str, str] = json.loads_dict(s)
print(parsed["name"])
print(parsed["age"])
