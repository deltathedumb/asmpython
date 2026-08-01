# probes: slice assignment through a parameter
# expect:
# [7, 8, 2]
def mutate(xs):
    xs[0:1] = [7, 8]


a = [1, 2]
mutate(a)
print(a)
