# probes: xs += through a parameter reaches the caller
# expect:
# [1, 2]
def mutate(xs):
    xs += [2]


a = [1]
mutate(a)
print(a)
