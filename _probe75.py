# probe75: dict merging, copying, nested dicts
d1 = {"a": 1, "b": 2}
d2 = {"b": 3, "c": 4}

# dict update (merge)
d1.update(d2)
print(d1["a"])   # 1
print(d1["b"])   # 3 (overwritten)
print(d1["c"])   # 4

# nested dict
config = {"db": {"host": "localhost", "port": 5432}, "app": {"debug": 0}}
print(config["db"]["host"])    # localhost
print(config["db"]["port"])    # 5432
print(config["app"]["debug"])  # 0

# dict.keys(), .values()
d = {"x": 10, "y": 20, "z": 30}
keys = list(d.keys())
vals = list(d.values())
print(len(keys))   # 3
print(len(vals))   # 3

total: int = 0
for v in d.values():
    total = total + v
print(total)   # 60

# dict copy
original = {"k": 42}
copy = dict(original)
copy["k"] = 99
print(original["k"])   # 42 (unchanged)
print(copy["k"])       # 99
