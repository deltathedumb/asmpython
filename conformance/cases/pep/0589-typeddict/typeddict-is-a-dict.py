# tier: spec
# ref: library/typing.html#typing.TypedDict
# expect:
# dict
# [('name', 'x'), ('year', 2000)]
# ['name', 'year']
# True
from typing import TypedDict

class Movie(TypedDict):
    name: str
    year: int

m: Movie = {"name": "x", "year": 2000}
print(type(m).__name__)
print(sorted(m.items()))
print(sorted(Movie.__annotations__))
print(Movie.__total__)
