# probes: an error raised while handling keeps __context__
# expect:
# second
# ValueError
try:
    try:
        raise ValueError("first")
    except ValueError:
        raise TypeError("second")
except TypeError as err:
    print(str(err))
    print(type(err.__context__).__name__)
