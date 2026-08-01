# tier: spec
# ref: reference/simple_stmts.html#the-raise-statement
# expect:
# None
# True
# ValueError
try:
    try:
        raise ValueError("inner")
    except ValueError:
        raise KeyError("outer") from None
except KeyError as e:
    print(e.__cause__)
    print(e.__suppress_context__)
    print(type(e.__context__).__name__)
