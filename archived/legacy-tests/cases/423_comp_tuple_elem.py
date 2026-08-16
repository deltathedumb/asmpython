# expect:
# 9
# (0, 0)
# (1, 1)
# (2, 2)
# 2
# (0, 1)

flat = [(x, y) for x in range(3) for y in range(3)]
print(len(flat))
print(flat[0])
print(flat[4])
print(flat[8])

pairs = [(x, y) for x in range(5) for y in range(5) if x + y == 1]
print(len(pairs))
print(pairs[0])
