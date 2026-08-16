# probes: a function mutates the caller's list
# expect:
# [1, 2]
# 2
def add(xs):
    xs.append(2)


a = [1]
add(a)
print(a)
print(len(a))
