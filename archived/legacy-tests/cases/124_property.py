# expect:
# Hello, Generic
# ...
# Hello, Generic says ...
# Hello, Rex
# Woof
# Hello, Rex says Woof
# 12
# 22
# 36


class Animal:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def greeting(self) -> str:
        return "Hello, " + self.name

    @property
    def sound(self) -> str:
        return "..."

    def describe(self) -> str:
        return self.greeting + " says " + self.sound


class Dog(Animal):
    @property
    def sound(self) -> str:
        return "Woof"


a = Animal("Generic")
print(a.greeting)
print(a.sound)
print(a.describe())

d = Dog("Rex")
print(d.greeting)
print(d.sound)
print(d.describe())


class Box:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h

    @property
    def area(self) -> int:
        return self.w * self.h


b = Box(3, 4)
print(b.area)
print(b.area + 10)
total = 0
for i in range(3):
    total += b.area
print(total)
