# isolate: catching KeyError
d = {"x": 1}
try:
    v = d["missing"]
except KeyError:
    print("caught_key")
