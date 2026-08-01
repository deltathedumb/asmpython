# tier: spec
# ref: library/sys.html#sys.implementation
# expect:
# str
# True
# True
# True
import sys

print(type(sys.implementation.name).__name__)
print(len(sys.implementation.version) >= 3)
print(sys.implementation.name == sys.implementation.name.lower())
print(isinstance(sys.implementation.hexversion, int))
