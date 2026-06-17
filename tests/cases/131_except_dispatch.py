# expect:
# key
# lookup idx
# tup v
# inner finally
# outer boom
# else 1
# value error

# 1. Multiple except clauses dispatch on the actual exception type, not
# just the first one.
try:
    raise KeyError("k")
except TypeError:
    print("type")
except KeyError:
    print("key")

# 2. `except LookupError` catches a raised IndexError via the builtin
# exception hierarchy.
try:
    raise IndexError("idx")
except LookupError as e:
    print("lookup", e)

# 3. `except (TypeError, ValueError)` -- a tuple of exception types.
try:
    raise ValueError("v")
except (TypeError, ValueError) as e:
    print("tup", e)

# 4. A non-matching except + finally: finally runs, then the exception
# propagates to the enclosing handler.
try:
    try:
        raise TypeError("boom")
    except ValueError:
        print("wrong")
    finally:
        print("inner finally")
except TypeError as e:
    print("outer", e)

# 5. try/except/else: else runs only when no exception was raised.
try:
    x = 1
except ValueError:
    print("err")
else:
    print("else", x)

# 6. A handler whose type doesn't match an internal raise (list.index ->
# ValueError) falls through to a later matching handler.
xs = [1, 2, 3]
try:
    xs.index(99)
except KeyError:
    print("wrong key")
except ValueError:
    print("value error")
