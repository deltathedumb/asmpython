d = {"type": "click", "x": 10}

# Manual test of what the match should do
t1 = "type" in d
t2 = d["type"] == "click"
t3 = "x" in d

print(t1)  # True
print(t2)  # True (mixed dict, so "click" comparison might fail?)
print(t3)  # True
print(t1 and t2 and t3)  # True?
