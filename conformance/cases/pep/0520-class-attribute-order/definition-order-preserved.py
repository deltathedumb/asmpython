# tier: spec
# ref: reference/datamodel.html#class-object-creation
# expect:
# ['b', 'a', 'm', 'z']
class C:
    b = 1
    a = 2
    def m(self):
        pass
    z = 3

names = [n for n in vars(C) if not n.startswith("_")]
print(names)
