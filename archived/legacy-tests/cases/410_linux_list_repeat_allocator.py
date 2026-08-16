# expect:
# 64
# 7

# A count large enough to exercise the list-repeat helper's header and backing
# buffer allocations. On System V AMD64, malloc's size must be passed in RDI.
values = [0] * 64
values[63] = 7
print(len(values))
print(values[63])
