# expect:
# caught_key
# caught_lookup
# caught_exception

d = {"x": 1}

try:
    v = d["missing"]
except KeyError:
    print("caught_key")

try:
    v = d["y"]
except LookupError:
    print("caught_lookup")

try:
    v = d["z"]
except Exception:
    print("caught_exception")
