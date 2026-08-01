# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True
# 1
# False
# 2
calls = []

def mid():
    calls.append("mid")
    return 5

print(1 < mid() < 10)
print(len(calls))
print(10 < mid() < 20)
print(len(calls))
