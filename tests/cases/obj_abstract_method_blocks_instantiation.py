# probes: an abstractmethod blocks instantiation
# expect:
# abstract refused
# 9
import abc


class Shape(abc.ABC):
    @abc.abstractmethod
    def area(self):
        ...


class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side * self.side


try:
    Shape()
    print("abstract instantiated")
except TypeError:
    print("abstract refused")
print(Square(3).area())
