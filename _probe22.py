# probe22: type annotations, dataclasses, class with methods
class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def dist_sq(self) -> int:
        return self.x * self.x + self.y * self.y

    def __str__(self) -> str:
        return "(" + str(self.x) + "," + str(self.y) + ")"

p = Point(3, 4)
print(p.x)
print(p.y)
print(p.dist_sq())
print(p)

# class with list field
class Stack:
    items: list

    def __init__(self) -> None:
        self.items = []

    def push(self, v: int) -> None:
        self.items.append(v)

    def pop(self) -> int:
        return self.items.pop()

    def size(self) -> int:
        return len(self.items)

s = Stack()
s.push(1)
s.push(2)
s.push(3)
print(s.size())
print(s.pop())
print(s.size())
