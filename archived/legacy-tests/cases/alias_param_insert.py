# probes: insert through a parameter reaches the caller
# expect:
# [0, 1]
def mutate(xs):
    xs.insert(0, 0)


a = [1]
mutate(a)
print(a)
