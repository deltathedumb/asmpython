# tier: spec
# ref: library/typing.html#typing.Required
# min-python: 3.11
# expect:
# [('name', 'x')]
# ['name']
# ['debug']
from typing import TypedDict, Required, NotRequired

class Config(TypedDict):
    name: Required[str]
    debug: NotRequired[bool]

c: Config = {"name": "x"}
print(sorted(c.items()))
print(sorted(Config.__required_keys__))
print(sorted(Config.__optional_keys__))
