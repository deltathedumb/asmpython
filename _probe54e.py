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
