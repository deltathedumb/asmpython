# probes: remove through a parameter reaches the caller
# expect:
# [2]
def mutate(xs):
    xs.remove(1)


a = [1, 2]
mutate(a)
print(a)
