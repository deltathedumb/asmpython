# tier: spec
# ref: reference/datamodel.html#basic-customization
# expect:
# Plain
# True False
# True
# True
# True
# True
# True
class Plain:
    pass

p = Plain()
print(type(p).__name__)
print(p == p, p != p)
print(isinstance(hash(p), int))
print(p.__class__ is Plain)
print(repr(p).startswith("<"))
print(str(p) == repr(p))
print(hasattr(p, "__dict__"))
