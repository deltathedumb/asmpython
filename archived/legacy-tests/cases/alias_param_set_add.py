# probes: set add through a parameter
# expect:
# 3
# [1, 2, 3]
def mutate(s):
    s.add(3)


a = {1, 2}
mutate(a)
print(len(a))
print(sorted(a))
