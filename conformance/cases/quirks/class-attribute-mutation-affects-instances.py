# tier: spec
# ref: reference/datamodel.html#class-instances
# expect:
# [1] [1]
# ['own'] [1] [1]
class C:
    shared = []

a, b = C(), C()
a.shared.append(1)
print(b.shared, C.shared)
a.shared = ["own"]
print(a.shared, b.shared, C.shared)
