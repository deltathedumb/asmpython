# probe23: inheritance, super, type checks
class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."

class Dog(Animal):
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "Woof"

class Cat(Animal):
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "Meow"

d = Dog("Rex")
c = Cat("Whiskers")
print(d.name)
print(d.speak())
print(c.name)
print(c.speak())

# list of animals
animals: list = [d, c]
for a in animals:
    print(a.speak())
