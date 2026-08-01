# tier: spec
# ref: reference/compound_stmts.html#sequence-patterns
# expect:
# [] empty
# [1] one:1
# [1, 2] two:1,2
# [1, 2, 3] head:1 rest:[2, 3]
# 'ab' not-a-sequence
# (1, 2) two:1,2
# 5 not-a-sequence
def f(v):
    match v:
        case []:
            return "empty"
        case [x]:
            return f"one:{x}"
        case [x, y]:
            return f"two:{x},{y}"
        case [x, *rest]:
            return f"head:{x} rest:{rest}"
        case _:
            return "not-a-sequence"

for v in ([], [1], [1, 2], [1, 2, 3], "ab", (1, 2), 5):
    print(repr(v), f(v))
