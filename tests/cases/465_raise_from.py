# expect:
# chained


try:
    raise ValueError("outer") from RuntimeError("inner")
except ValueError:
    print("chained")
