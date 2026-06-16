# probe34: try/except with re-raise, chained exceptions, exception message
try:
    raise ValueError("test error")
except ValueError as e:
    msg = str(e)
    print(msg)   # test error (or similar)

# Raise from within except
def safe_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        raise ValueError("not a number: " + s)

try:
    v = safe_int("abc")
except ValueError as e:
    print("caught2")

# Exception hierarchy
try:
    raise IndexError("out of range")
except Exception:
    print("caught_exception")

# KeyError on dict access
d = {"x": 1}
try:
    v2 = d["missing"]
except KeyError:
    print("caught_key")
