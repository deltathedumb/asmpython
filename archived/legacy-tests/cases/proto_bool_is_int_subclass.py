# probes: bool is a subclass of int
# expect:
# True
# True
# 2
# 2
print(issubclass(bool, int))
print(isinstance(True, int))
print(True + True)
print(sum([True, False, True]))
