# probes: __class_getitem__ makes a class subscriptable
# expect:
# Container[int]
class Container:
    def __class_getitem__(cls, item):
        return "Container[" + item.__name__ + "]"


print(Container[int])
