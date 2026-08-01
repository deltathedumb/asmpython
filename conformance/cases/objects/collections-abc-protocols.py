# tier: spec
# ref: library/collections.abc.html
# expect:
# True True
# True True
# True True
# False
# True True
from collections.abc import Iterable, Sized, Sequence, Mapping, Hashable

print(isinstance([1], Iterable), isinstance([1], Sized))
print(isinstance([1], Sequence), isinstance({}, Mapping))
print(isinstance("a", Sequence), isinstance(1, Hashable))
print(isinstance([1], Hashable))
print(issubclass(dict, Mapping), issubclass(list, Sequence))
