# expect:
# 2
# 4
# False
# True
# ['b', 'c']

d: dict[str, int] = {"a": 1, "b": 2, "c": 3}

doubled = {k: v * 2 for k, v in d.items()}
print(doubled["a"])
print(doubled["b"])

big = {k: v for k, v in d.items() if v > 1}
print("a" in big)
print("b" in big)

keys = [k for k, v in d.items() if v >= 2]
print(keys)
