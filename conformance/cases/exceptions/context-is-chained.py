# tier: spec
# ref: reference/datamodel.html#exception-context
# expect:
# KeyError
# ValueError
# None
try:
    try:
        raise ValueError("inner")
    except ValueError:
        raise KeyError("outer")
except KeyError as e:
    print(type(e).__name__)
    print(type(e.__context__).__name__)
    print(e.__cause__)
