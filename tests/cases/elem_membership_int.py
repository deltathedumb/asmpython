# probes: `in` compares against each element (int elements)
# expect:
# True
# False
# True
xs = [10, 20, 30, 40]
print(20 in xs)
print(99 in xs)
print(99 not in xs)
