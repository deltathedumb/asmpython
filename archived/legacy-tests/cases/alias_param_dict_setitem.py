# probes: dict insert through a parameter
# expect:
# 2
# ['k', 'new']
def mutate(d):
    d["new"] = 1


a = {"k": 0}
mutate(a)
print(len(a))
print(sorted(a.keys()))
