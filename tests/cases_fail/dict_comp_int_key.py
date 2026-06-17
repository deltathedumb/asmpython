# expect-error: dict comprehension keys must be strings
d: dict[str, int] = {"a": 1, "b": 2}
inv = {v: k for k, v in d.items()}
print(inv)
