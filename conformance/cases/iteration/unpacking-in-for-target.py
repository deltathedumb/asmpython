# tier: spec
# ref: reference/compound_stmts.html#the-for-statement
# expect:
# 1 a
# 2 b
# 1 a b
# 0 1 a
# 1 2 b
pairs = [(1, "a"), (2, "b")]
for n, s in pairs:
    print(n, s)
for n, (s, t) in [(1, ("a", "b"))]:
    print(n, s, t)
for i, (a, b) in enumerate(pairs):
    print(i, a, b)
