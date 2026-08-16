# expect:
# {"name": "alice", "city": "NYC"}
# alice
# NYC
# ["1", "2", "3"]
# hello world

import json

d: dict[str, str] = {"name": "alice", "city": "NYC"}
s: str = json.dumps_dict(d)
print(s)

d2: dict[str, str] = json.loads_dict(s)
print(d2["name"])
print(d2["city"])

print(json.dumps_list(["1", "2", "3"]))
print(json.loads('"hello world"'))
