# probe46: exception hierarchy and chaining
try:
    raise ValueError("bad value")
except ValueError as e:
    print("caught ValueError")

# LookupError catches KeyError and IndexError
try:
    d: dict[str, int] = {}
    x = d["missing"]
except LookupError:
    print("caught LookupError")

# Multiple except clauses
def risky(n: int) -> int:
    if n == 0:
        raise ZeroDivisionError("zero")
    if n < 0:
        raise ValueError("negative")
    return n * 2

for v in [2, 0, -1]:
    try:
        print(risky(v))
    except ZeroDivisionError:
        print("zero div")
    except ValueError:
        print("value err")

# finally
try:
    x = 1 // 1
finally:
    print("finally")
print("after")
