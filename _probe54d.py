d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
keys = [k for k, v in d.items() if v >= 2]
print(keys)  # ['b', 'c']
