# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# ZeroDivisionError
# [1.0, 'inf', 0.5]
try:
    [1 / v for v in (1, 0)]
except ZeroDivisionError:
    print("ZeroDivisionError")

def safe(v):
    try:
        return 1 / v
    except ZeroDivisionError:
        return "inf"

print([safe(v) for v in (1, 0, 2)])
