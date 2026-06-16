d = {"type": "click", "x": 10, "y": 20}

# Test just the basic dict in-check
print("type" in d)   # True
print(d["type"])     # click

# Simple mapping match
match d:
    case {"type": "click"}:
        print("click matched")
    case _:
        print("no match")
