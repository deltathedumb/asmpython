# probes: raise ... from ... records __cause__
# expect:
# outer
# ValueError
# inner
try:
    try:
        raise ValueError("inner")
    except ValueError as inner:
        raise TypeError("outer") from inner
except TypeError as outer:
    print(str(outer))
    print(type(outer.__cause__).__name__)
    print(str(outer.__cause__))
