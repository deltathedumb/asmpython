# probes: a dict inside a dict keeps its own entries
# expect:
# {'inner': 1, 'other': 'two'}
# 1
# two
config = {"outer": {"inner": 1, "other": "two"}}
print(config["outer"])
print(config["outer"]["inner"])
print(config["outer"]["other"])
