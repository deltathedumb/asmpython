# tier: spec
# ref: library/stdtypes.html#the-null-object
# expect:
# True
# True True
# True True
# True
# True
# True
print(None is None)
print(True is True, False is False)
print(bool(1) is True, bool(0) is False)
print((1 == 1) is True)
print(... is Ellipsis)
print(NotImplemented is NotImplemented)
