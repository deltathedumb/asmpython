d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
doubled = {k: v * 2 for k, v in d.items()}
print(doubled["a"])  # 2

big = {k: v for k, v in d.items() if v > 1}
print("a" in big)  # False

keys = [k for k, v in d.items() if v >= 2]
print(keys)  # ['b', 'c']

counts: dict[str, int] = {}
words = ["hello", "world", "hello"]
for w in words:
    if w in counts:
        counts[w] = counts[w] + 1
    else:
        counts[w] = 1
print(counts["hello"])  # 2
