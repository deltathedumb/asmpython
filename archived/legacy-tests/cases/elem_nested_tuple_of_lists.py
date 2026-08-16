# probes: a list inside a tuple stays mutable and intact
# expect:
# ([1, 2, 3], ['a'])
# [1, 2, 3]
# a
holder = ([1, 2], ["a"])
holder[0].append(3)
print(holder)
print(holder[0])
print(holder[1][0])
