d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
big = {k: v for k, v in d.items() if v > 1}
print("a" in big)  # False
print("b" in big)  # True
