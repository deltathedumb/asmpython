# tier: spec
# ref: reference/expressions.html#attribute-references
# expect:
# deep
# deep
# deep
# AttributeError
class Inner:
    v = "deep"

class Outer:
    inner = Inner()

o = Outer()
print(o.inner.v)
print(Outer.inner.v)
print(getattr(getattr(o, "inner"), "v"))
try:
    o.missing.v
except AttributeError:
    print("AttributeError")
