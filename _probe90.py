# probe90: complex exception patterns, exception chaining, sys.exit
import sys

# Exception groups / chaining
def process(items: list) -> list:
    results = []
    for item in items:
        try:
            if item < 0:
                raise ValueError("negative: " + str(item))
            results.append(item * 2)
        except ValueError as e:
            results.append(-1)
    return results

out = process([1, -2, 3, -4, 5])
print(out[0])   # 2
print(out[1])   # -1
print(out[2])   # 6
print(out[3])   # -1
print(out[4])   # 10

# Exception with args
try:
    raise RuntimeError("test error", 42)
except RuntimeError as e:
    print(str(e))   # ('test error', 42) or similar

# Nested try-except
def nested() -> str:
    try:
        try:
            raise ValueError("inner")
        except TypeError:
            return "wrong"
        return "no exception"
    except ValueError as e:
        return "caught: " + str(e)

print(nested())   # caught: inner
