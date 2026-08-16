# probes: append through a parameter reaches the caller
# expect:
# 2
# [1, 2]
def mutate(xs):
    xs.append(2)


a = [1]
mutate(a)
print(len(a))
print(a)
