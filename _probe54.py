# probe54: dict comprehensions from items
d: dict[str, int] = {"a": 1, "b": 2, "c": 3}

# Inverted dict
inv = {v: k for k, v in d.items()}
# Note: values are ints, can't be dict keys
# So try key: str result
doubled = {k: v * 2 for k, v in d.items()}
print(doubled["a"])  # 2
print(doubled["b"])  # 4

# Filtered dict comp
big = {k: v for k, v in d.items() if v > 1}
print("a" in big)  # False
print("b" in big)  # True

# list comp from dict items
keys = [k for k, v in d.items() if v >= 2]
print(keys)  # ['b', 'c']

# Nested dict comp
counts: dict[str, int] = {}
words = ["hello", "world", "hello", "foo", "hello", "world"]
for w in words:
    if w in counts:
        counts[w] = counts[w] + 1
    else:
        counts[w] = 1
print(counts["hello"])  # 3
print(counts["world"])  # 2
print(counts["foo"])    # 1
