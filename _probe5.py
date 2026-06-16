# probe: more patterns

# 1. Exception handling with specific exception types
def safe_div(a: int, b: int) -> int:
    try:
        return a // b
    except ZeroDivisionError:
        return -1

print(safe_div(10, 2))
print(safe_div(10, 0))

# 2. raise with message
def check_positive(n: int) -> None:
    if n < 0:
        raise ValueError("must be positive")

try:
    check_positive(-1)
except ValueError as e:
    print("caught")

# 3. Multiple except clauses
def parse_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1

print(parse_int("42"))
print(parse_int("abc"))

# 4. Finally
def with_finally() -> int:
    try:
        return 1
    finally:
        pass  # no-op

print(with_finally())
