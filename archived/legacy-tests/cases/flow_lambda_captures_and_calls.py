# probes: a lambda closes over an enclosing name
# expect:
# 12
# 3
def make_scaler(factor):
    return lambda v: v * factor


print(make_scaler(3)(4))
print((lambda a, b=2: a + b)(1))
