# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# KeyError
# ValueError
try:
    try:
        raise ValueError("original")
    finally:
        raise KeyError("from-finally")
except KeyError as e:
    print(type(e).__name__)
    print(type(e.__context__).__name__)
