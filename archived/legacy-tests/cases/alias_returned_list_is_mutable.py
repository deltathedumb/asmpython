# probes: a returned list can be mutated by the caller
# expect:
# [1, 2]
# 2
def build():
    return [1]


made = build()
made.append(2)
print(made)
print(len(made))
