# probes: mutating an inner list through a parameter
# expect:
# [[1, 9], [2]]
# 2
def mutate(rows):
    rows[0].append(9)


a = [[1], [2]]
mutate(a)
print(a)
print(len(a[0]))
