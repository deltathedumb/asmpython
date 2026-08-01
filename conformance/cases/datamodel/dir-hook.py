# tier: spec
# ref: reference/datamodel.html#object.__dir__
# expect:
# ['a', 'a', 'b']
# True
class C:
    def __dir__(self):
        return ["b", "a", "a"]

print(dir(C()))
class D:
    x = 1
print("x" in dir(D()))
