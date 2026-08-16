# probes: clearing through an alias empties both names
# expect:
# 0
# []
a = [1, 2, 3]
b = a
b.clear()
print(len(a))
print(a)
