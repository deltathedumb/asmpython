d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
doubled = {k: v * 2 for k, v in d.items()}
print(doubled["a"])  # 2
print(doubled["b"])  # 4
