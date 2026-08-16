# probes: a local alias of a parameter still aliases
# expect:
# [1, 2]
def mutate(xs):
    local = xs
    local.append(2)


a = [1]
mutate(a)
print(a)
