# probes: json.loads preserves each nested value kind
# expect:
# int
# float
# str
# True
# None
import json

obj = json.loads('{"n": 1, "f": 2.5, "s": "x", "b": true, "z": null}')
print(type(obj["n"]).__name__)
print(type(obj["f"]).__name__)
print(type(obj["s"]).__name__)
print(obj["b"])
print(obj["z"])
