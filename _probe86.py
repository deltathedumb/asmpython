# probe86: abstract methods / interface patterns, multiple inheritance
class Animal:
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."

    def describe(self) -> str:
        return self.name + " says " + self.speak()

class Dog(Animal):
    def speak(self) -> str:
        return "woof"

class Cat(Animal):
    def speak(self) -> str:
        return "meow"

class GuideDog(Dog):
    def __init__(self, name: str, owner: str) -> None:
        super().__init__(name)
        self.owner = owner

    def describe(self) -> str:
        return super().describe() + " (guides " + self.owner + ")"

d = Dog("Rex")
c = Cat("Whiskers")
g = GuideDog("Buddy", "Alice")

print(d.describe())   # Rex says woof
print(c.describe())   # Whiskers says meow
print(g.describe())   # Buddy says woof (guides Alice)

# isinstance checks
print(isinstance(g, Dog))     # True
print(isinstance(g, Animal))  # True
print(isinstance(g, Cat))     # False

# List of mixed animals
animals = [d, c, g]
for a in animals:
    print(a.speak(), end=" ")
print()   # woof meow woof
