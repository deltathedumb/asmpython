# tier: spec
# ref: library/functions.html#callable
# expect:
# True False True
# True True
# True True
# NoneType ellipsis
# NotImplementedType
print(callable(print), callable(1), callable(str))
print(type(1) is int, type("a") is str)
print(isinstance(1, object), issubclass(bool, int))
print(type(None).__name__, type(...).__name__)
print(type(NotImplemented).__name__)
