# tier: spec
# ref: reference/datamodel.html#object.__len__
# expect:
# 3
# 10
# [0, 10, 20]
# True
class Seq:
    def __len__(self):
        return 3
    def __getitem__(self, i):
        if i >= 3:
            raise IndexError
        return i * 10

s = Seq()
print(len(s))
print(s[1])
print(list(s))
print(20 in s)
