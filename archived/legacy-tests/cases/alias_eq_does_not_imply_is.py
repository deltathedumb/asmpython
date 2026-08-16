# probes: equality never implies identity
# expect:
# True
a = [1, 2]
b = [1, 2]
print(a == b and a is not b)
