# tier: spec
# ref: reference/datamodel.html#determining-the-appropriate-metaclass
# expect:
# TypeError
# ['Ok', 'B', 'A', 'object']
class A:
    pass

class B(A):
    pass

try:
    class Bad(A, B):
        pass
except TypeError:
    print("TypeError")

class Ok(B, A):
    pass

print([k.__name__ for k in Ok.__mro__])
