# tier: spec
# ref: reference/datamodel.html#object.__iadd__
# expect:
# iadd
# add-fallback
class WithIadd:
    def __iadd__(self, o):
        return "iadd"

class OnlyAdd:
    def __add__(self, o):
        return "add-fallback"

a = WithIadd()
a += 1
print(a)
b = OnlyAdd()
b += 1
print(b)
