# tier: spec
# ref: library/typing.html#typing.ReadOnly
# min-python: 3.13
# expect:
# [('fixed', 1), ('mutable', 'a')]
# ['fixed']
# ['mutable']
from typing import TypedDict, ReadOnly

class C(TypedDict):
    fixed: ReadOnly[int]
    mutable: str

v: C = {"fixed": 1, "mutable": "a"}
print(sorted(v.items()))
print(sorted(C.__readonly_keys__))
print(sorted(C.__mutable_keys__))
