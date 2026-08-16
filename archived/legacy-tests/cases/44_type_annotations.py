# expect:
# 7
# 30
# 100
# hi
def add(x: int, y: int = 2) -> int:
    return x + y

print(add(5))
print(add(10, 20))

# Union and generic annotations are tolerated -- parser eats them but they
# don't influence inference yet.
def square(n: int | None = 10) -> int:
    return n * n

print(square())

# Keyword-only marker `*` is accepted; we still call positionally.
def greet(name: str = "hi", *, mode: str = "default") -> int:
    print(name)
    return 0

greet()
