# probes: extend through a parameter reaches the caller
# expect:
# 3
# [1, 2, 3]
def mutate(xs):
    xs.extend([2, 3])


a = [1]
mutate(a)
print(len(a))
print(a)
