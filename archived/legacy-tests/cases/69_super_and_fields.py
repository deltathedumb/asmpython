# expect:
# woof
# name=Rex legs=4 tail=1
# Rex (4 legs)
# generic sound
class Animal:
    # asmpython uses static method dispatch (no RTTI/vtables): a method resolves
    # on the receiver's static type. This test exercises super().__init__ (a
    # subclass inheriting base fields) and super().method() (a base method
    # invoked explicitly), plus str / inherited fields read back out — none of
    # which need virtual dispatch.
    def __init__(self, name: str, legs: int):
        self.name = name
        self.legs = legs

    def sound(self) -> str:
        return "generic sound"

    def describe(self) -> str:
        return self.name


class Dog(Animal):
    def __init__(self, name: str, legs: int, tail: int):
        super().__init__(name, legs)
        self.tail = tail

    def sound(self) -> str:
        return "woof"

    def describe(self) -> str:
        base = super().describe()
        return base + " (" + str(self.legs) + " legs)"


d = Dog("Rex", 4, 1)
print(d.sound())
print("name=" + d.name + " legs=" + str(d.legs) + " tail=" + str(d.tail))
print(d.describe())
a = Animal("thing", 2)
print(a.sound())
