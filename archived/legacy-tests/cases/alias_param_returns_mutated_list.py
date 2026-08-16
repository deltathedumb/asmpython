# probes: the callee's own view of the mutation
# expect:
# 2
# [1, 2]
# 2
# True
def mutate(xs):
    xs.append(2)
    return xs


a = [1]
returned = mutate(a)
print(len(returned))
print(returned)
print(len(a))
print(returned is a)
