# expect:
# Animal: Buddy speaks
# Dog: Buddy says Woof
# Cat: Whiskers says Meow

class Animal:
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return self.name + " speaks"

class Dog(Animal):
    def speak(self) -> str:
        return "Buddy says Woof"

class Cat(Animal):
    def speak(self) -> str:
        return "Whiskers says Meow"

a = Animal("Buddy")
print("Animal: " + a.speak())
d = Dog("Buddy")
print("Dog: " + d.speak())
c = Cat("Whiskers")
print("Cat: " + c.speak())
