# probes: two equal lists are not the same object
# expect:
# False
# True
# True
a = [1]
b = [1]
print(a is b)
print(a == b)
print(a is not b)
