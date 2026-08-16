# probes: sort through a parameter reaches the caller
# expect:
# [1, 2, 3]
def mutate(xs):
    xs.sort()


a = [3, 1, 2]
mutate(a)
print(a)
