# tier: spec
# ref: library/functions.html#isinstance
# expect:
# True
# True
# False
# int | str
# Union
print(isinstance(1, int | str))
print(isinstance("a", int | str))
print(isinstance(1.5, int | str))
print(int | str)
print(type(int | str).__name__)
