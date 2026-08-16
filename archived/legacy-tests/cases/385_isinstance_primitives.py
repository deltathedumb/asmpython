# expect:
# True
# False
# True
# False
# True
# False
# True

x: int = 42
print(isinstance(x, int))
print(isinstance(x, str))

s: str = "hello"
print(isinstance(s, str))
print(isinstance(s, int))

xs: list[int] = [1, 2, 3]
print(isinstance(xs, list))
print(isinstance(xs, dict))

d: dict[str, int] = {"a": 1}
print(isinstance(d, dict))
