# probes: pop through a parameter reaches the caller
# expect:
# 2
# [1]
def mutate(xs):
    return xs.pop()


a = [1, 2]
print(mutate(a))
print(a)
