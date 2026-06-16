# expect:
# 0
# 1
# 2

items: list[str] = ["a", "b", "c"]
indices: list[int] = [i for i, _ in enumerate(items)]
for idx in indices:
    print(idx)
