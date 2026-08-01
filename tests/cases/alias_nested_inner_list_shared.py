# probes: an inner list is shared with its container
# expect:
# [[1, 2]]
# 2
inner = [1]
outer = [inner]
inner.append(2)
print(outer)
print(len(outer[0]))
