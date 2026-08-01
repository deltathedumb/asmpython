# tier: spec
# ref: reference/executionmodel.html#private-name-mangling
# expect:
# 1
# 1
# False
# ['_C__hidden']
class C:
    def __init__(self):
        self.__hidden = 1
    def peek(self):
        return self.__hidden

c = C()
print(c.peek())
print(c._C__hidden)
print(hasattr(c, "__hidden"))
print([n for n in sorted(vars(c)) if "hidden" in n])
