# probes: two instances are distinct objects
# expect:
# False
# True
class Plain:
    pass


print(Plain() is Plain())
a = Plain()
print(a is a)
