# probes: a function mutates a global list without a parameter
# expect:
# [1, 2]
shared = [1]


def mutate():
    shared.append(2)


mutate()
print(shared)
