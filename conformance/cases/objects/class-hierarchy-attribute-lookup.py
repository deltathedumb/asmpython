# tier: spec
# ref: reference/datamodel.html#class-instances
# expect:
# A
# B A
# inst B A
class A:
    shared = "A"

class B(A):
    pass

b = B()
print(b.shared)
B.shared = "B"
print(b.shared, A.shared)
b.shared = "inst"
print(b.shared, B.shared, A.shared)
