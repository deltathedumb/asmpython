# probe21: exception handling patterns
# 1. raise with custom message
try:
    raise ValueError("bad value")
except ValueError as e:
    print("caught")

# 2. finally
def try_finally() -> int:
    try:
        return 1
    finally:
        pass
print(try_finally())

# 3. multiple except
def parse_it(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1

print(parse_it("42"))
print(parse_it("abc"))

# 4. nested try
try:
    try:
        x = 1 // 0
    except ZeroDivisionError:
        print("inner_caught")
except Exception:
    print("outer_caught")
