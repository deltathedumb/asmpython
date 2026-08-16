# expect:
# 0
# 1
# 2
# b
# 3

keys: list[str] = ["a", "b", "c"]
d = {k: i for i, k in enumerate(keys)}
print(d["a"])
print(d["b"])
print(d["c"])

# reverse lookup via comprehension
rev = {str(i): k for i, k in enumerate(keys)}
print(rev["1"])
print(len(rev))
