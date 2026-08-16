# probes: element assignment through a parameter
# expect:
# [99, 2]
def mutate(xs):
    xs[0] = 99


a = [1, 2]
mutate(a)
print(a)
