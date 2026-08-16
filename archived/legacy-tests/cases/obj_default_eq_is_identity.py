# probes: without __eq__, equality is identity
# expect:
# True
# False
class Plain:
    pass


a = Plain()
b = Plain()
print(a == a)
print(a == b)
