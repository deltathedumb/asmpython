# tier: spec
# ref: reference/expressions.html#subscriptions
# expect:
# 30
# 10
# IndexError
xs = [10, 20, 30]
print(xs[-1])
print(xs[-3])
try:
    xs[-4]
except IndexError as e:
    print(type(e).__name__)
