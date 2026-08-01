# tier: spec
# ref: library/abc.html
# expect:
# TypeError
# area=9
# True
# ['area']
from abc import ABC, abstractmethod

class Shape(ABC):
    @abstractmethod
    def area(self):
        ...
    def describe(self):
        return "area=" + str(self.area())

try:
    Shape()
except TypeError:
    print("TypeError")

class Square(Shape):
    def __init__(self, n):
        self.n = n
    def area(self):
        return self.n ** 2

print(Square(3).describe())
print(isinstance(Square(1), Shape))
print(sorted(Shape.__abstractmethods__))
