# tier: spec
# ref: reference/datamodel.html#custom-classes
# expect:
# A Bw
# B
# ['C', 'A', 'B', 'object']
# ['D', 'B', 'A', 'object']
class A:
    v = "A"
class B:
    v = "B"
    w = "Bw"
class C(A, B):
    pass
class D(B, A):
    pass

print(C.v, C.w)
print(D.v)
print([k.__name__ for k in C.__mro__])
print([k.__name__ for k in D.__mro__])
