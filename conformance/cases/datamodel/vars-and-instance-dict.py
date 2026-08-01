# tier: spec
# ref: library/functions.html#vars
# expect:
# ['inst']
# 2
# False
# True
class C:
    cls_attr = 1
    def __init__(self):
        self.inst = 2

c = C()
print(sorted(vars(c)))
print(vars(c)["inst"])
print("cls_attr" in vars(c))
print("cls_attr" in vars(C))
