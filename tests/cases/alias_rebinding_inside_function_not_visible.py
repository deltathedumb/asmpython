# probes: rebinding a parameter does not reach the caller
# expect:
# [1]
# [9, 9]
def rebind(xs):
    xs = [9, 9]
    return xs


a = [1]
result = rebind(a)
print(a)
print(result)
