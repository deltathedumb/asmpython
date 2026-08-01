# tier: spec
# ref: reference/datamodel.html#object.__class_getitem__
# expect:
# C[<class 'int'>]
# list[int]
# dict[str, int]
class C:
    def __class_getitem__(cls, item):
        return f"{cls.__name__}[{item}]"

print(C[int])
print(list[int])
print(dict[str, int])
