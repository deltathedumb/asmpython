# tier: spec
# ref: reference/simple_stmts.html#the-raise-statement
# expect:
# KeyError
# ValueError
# True
try:
    try:
        raise ValueError("root")
    except ValueError as e:
        raise KeyError("wrapper") from e
except KeyError as k:
    print(type(k).__name__)
    print(type(k.__cause__).__name__)
    print(k.__suppress_context__)
