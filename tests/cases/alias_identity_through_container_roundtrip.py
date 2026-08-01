# probes: identity survives a container round trip
# expect:
# True
# True
a = [1]
box = [a]
print(box[0] is a)
holder = {"v": a}
print(holder["v"] is a)
