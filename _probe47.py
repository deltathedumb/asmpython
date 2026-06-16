# probe47: class inheritance
class Animal:
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."

    def describe(self) -> str:
        return f"{self.name} says {self.speak()}"

class Dog(Animal):
    def speak(self) -> str:
        return "woof"

class Cat(Animal):
    def speak(self) -> str:
        return "meow"

d = Dog("Rex")
c = Cat("Whiskers")

print(d.describe())   # Rex says woof
print(c.describe())   # Whiskers says meow
print(d.name)         # Rex
print(isinstance(d, Dog))    # True
print(isinstance(d, Animal)) # True
print(isinstance(d, Cat))    # False

# super()
class Puppy(Dog):
    def speak(self) -> str:
        base = super().speak()
        return base + "!"

p = Puppy("Buddy")
print(p.describe())   # Buddy says woof!
