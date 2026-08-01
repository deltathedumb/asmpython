# probes: dict update through a parameter
# expect:
# 2
def mutate(d):
    d.update({"j": 2})


a = {"k": 1}
mutate(a)
print(len(a))
