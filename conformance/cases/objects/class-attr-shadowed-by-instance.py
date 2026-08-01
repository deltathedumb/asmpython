# tier: spec
# ref: reference/datamodel.html#class-instances
# expect:
# instance
# class
# class
class C:
    shared = "class"

a = C()
b = C()
a.shared = "instance"
print(a.shared)
print(b.shared)
print(C.shared)
