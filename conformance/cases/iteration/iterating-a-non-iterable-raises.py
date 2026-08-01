# tier: spec
# ref: reference/compound_stmts.html#the-for-statement
# expect:
# int TypeError
# str ['a', 'b']
# list [1]
# dict [1]
# set [1]
# range [0]
for v in (1, "ab", [1], {1: 2}, {1}, range(1)):
    try:
        print(type(v).__name__, list(iter(v)))
    except TypeError:
        print(type(v).__name__, "TypeError")
