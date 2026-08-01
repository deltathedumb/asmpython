# tier: spec
# ref: reference/executionmodel.html#resolution-of-names
# expect:
# [2, 4, 6]
# NameError
class C:
    values = [1, 2, 3]
    doubled = [v * 2 for v in values]
    try:
        bad = [v * len(values) for v in range(2)]
    except NameError:
        bad = "NameError"

print(C.doubled)
print(C.bad)
