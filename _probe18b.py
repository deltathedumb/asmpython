# dict comprehension from zip
keys = ["a", "b", "c"]
vals = [1, 2, 3]
d = {k: v for k, v in zip(keys, vals)}
print(d.get("b"))
print(d.get("c"))
