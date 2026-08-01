# probes: reverse through a parameter reaches the caller
# expect:
# [3, 2, 1]
def mutate(xs):
    xs.reverse()


a = [1, 2, 3]
mutate(a)
print(a)
