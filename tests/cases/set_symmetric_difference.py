# expect:
# [1, 3]
print(sorted({1, 2} ^ {2, 3}))
# set symmetric-difference ^ is rejected ([E013] unsupported operand ^).
