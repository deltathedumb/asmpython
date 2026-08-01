# tier: spec
# ref: library/functions.html#getattr
# expect:
# 1
# default
# AttributeError
# True
# False
class C:
    pass

c = C()
setattr(c, "v", 1)
print(getattr(c, "v"))
print(getattr(c, "missing", "default"))
try:
    getattr(c, "missing")
except AttributeError:
    print("AttributeError")
print(hasattr(c, "v"))
delattr(c, "v")
print(hasattr(c, "v"))
