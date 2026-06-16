d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
inv = {v: k for k, v in d.items()}
print(inv)
