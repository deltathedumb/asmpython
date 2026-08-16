# probes: clear through a parameter reaches the caller
# expect:
# 0
# []
def mutate(xs):
    xs.clear()


a = [1, 2]
mutate(a)
print(len(a))
print(a)
