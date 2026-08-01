# probes: a tuple is immutable but its elements are not
# expect:
# ([1],)
# 1
holder = ([],)
holder[0].append(1)
print(holder)
print(len(holder[0]))
