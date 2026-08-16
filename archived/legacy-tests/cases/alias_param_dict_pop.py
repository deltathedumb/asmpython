# probes: dict pop through a parameter
# expect:
# 5
# 1
def mutate(d):
    return d.pop("k")


a = {"k": 5, "j": 6}
print(mutate(a))
print(len(a))
