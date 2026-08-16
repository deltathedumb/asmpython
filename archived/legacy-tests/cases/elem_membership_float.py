# probes: `in` compares against each element (float elements)
# expect:
# True
# False
# True
xs = [1.5, 2.5, 3.5, 4.5]
print(2.5 in xs)
print(9.5 in xs)
print(9.5 not in xs)
