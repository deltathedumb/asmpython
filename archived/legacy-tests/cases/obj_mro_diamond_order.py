# probes: __mro__ is the C3 linearization
# expect:
# ['D', 'B', 'C', 'A', 'object']
class A:
    pass


class B(A):
    pass


class C(A):
    pass


class D(B, C):
    pass


print([cls.__name__ for cls in D.__mro__])
