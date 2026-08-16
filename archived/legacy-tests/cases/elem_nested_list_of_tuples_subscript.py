# probes: an element of a tuple inside a list survives the outer read
# expect:
# (1, 'a')
# 1
# a
# b
rows = [(1, "a"), (2, "b")]
print(rows[0])
print(rows[0][0])
print(rows[0][1])
print(rows[1][1])
