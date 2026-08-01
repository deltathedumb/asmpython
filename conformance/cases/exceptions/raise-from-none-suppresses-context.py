# tier: spec
# ref: reference/simple_stmts.html#the-raise-statement
# expect:
# KeyError
# None
# True
# ValueError
try:
    try:
        raise ValueError("root")
    except ValueError:
        raise KeyError("wrapper") from None
except KeyError as k:
    print(type(k).__name__)
    print(k.__cause__)
    print(k.__suppress_context__)
    print(type(k.__context__).__name__)
