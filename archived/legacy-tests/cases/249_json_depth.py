# expect:
# hello
# 42
# True
# None
# {"name": "alice", "age": "30"}
# {"x": 1, "y": 2}
# ["a", "b", "c"]
# [1, 2, 3]
# alice
# 30

import json

print(json.loads('"hello"'))
print(json.loads("42"))
print(json.loads("true"))
print(json.loads("null"))

d: dict[str, str] = {"name": "alice", "age": "30"}
print(json.dumps_dict(d))

di: dict[str, int] = {"x": 1, "y": 2}
print(json.dumps_dict_int(di))

ls: list[str] = ["a", "b", "c"]
print(json.dumps_list(ls))

li: list[int] = [1, 2, 3]
print(json.dumps_list_int(li))

parsed = json.loads_dict('{"name": "alice", "age": "30"}')
name: str = parsed["name"]
age: str = parsed["age"]
print(name)
print(age)
