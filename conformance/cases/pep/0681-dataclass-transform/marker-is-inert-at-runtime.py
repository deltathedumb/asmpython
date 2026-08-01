# tier: spec
# ref: library/typing.html#typing.dataclass_transform
# min-python: 3.11
# expect:
# C
# True
from typing import dataclass_transform

@dataclass_transform()
def model(cls):
    return cls

@model
class C:
    x: int

print(C.__name__)
print(model.__dataclass_transform__["eq_default"])
