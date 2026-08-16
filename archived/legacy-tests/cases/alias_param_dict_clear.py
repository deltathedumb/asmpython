# probes: dict clear through a parameter
# expect:
# 0
def mutate(d):
    d.clear()


a = {"k": 1}
mutate(a)
print(len(a))
