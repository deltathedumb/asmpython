# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ValueError
# NameError
try:
    raise ValueError("x")
except ValueError as e:
    print(type(e).__name__)
try:
    print(e)
except NameError:
    print("NameError")
