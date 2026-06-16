# probe112: complex class scenarios - mixins, interface-like patterns
class Printable:
    def to_str(self) -> str:
        return "Printable"

    def print_self(self) -> None:
        print(self.to_str())

class Comparable:
    def compare(self, other: "Comparable") -> int:
        return 0   # equal by default

class Person(Printable, Comparable):
    def __init__(self, name: str, age: int) -> None:
        self.name = name
        self.age = age

    def to_str(self) -> str:
        return self.name + "(" + str(self.age) + ")"

    def compare(self, other: "Person") -> int:
        if self.age < other.age:
            return -1
        if self.age > other.age:
            return 1
        return 0

p1 = Person("Alice", 30)
p2 = Person("Bob", 25)
p1.print_self()   # Alice(30)
p2.print_self()   # Bob(25)
print(p1.compare(p2))   # 1 (Alice is older)
print(p2.compare(p1))   # -1

# Class with class method factory
class Shape:
    def __init__(self, kind: str, size: int) -> None:
        self.kind = kind
        self.size = size

    @classmethod
    def circle(cls, r: int) -> "Shape":
        return cls("circle", r)

    @classmethod
    def square(cls, s: int) -> "Shape":
        return cls("square", s)

    def area(self) -> int:
        if self.kind == "circle":
            return 3 * self.size * self.size   # approx
        return self.size * self.size

c = Shape.circle(5)
s = Shape.square(4)
print(c.area())   # 75
print(s.area())   # 16
