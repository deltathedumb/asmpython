# tier: spec
# ref: reference/datamodel.html#object.__reversed__
# expect:
# ['z', 'y']
# [20, 10, 0]
class C:
    def __reversed__(self):
        return iter(["z", "y"])
    def __len__(self):
        return 2
    def __getitem__(self, i):
        return "ab"[i]

print(list(reversed(C())))

class Seq:
    def __len__(self):
        return 3
    def __getitem__(self, i):
        return i * 10

print(list(reversed(Seq())))
