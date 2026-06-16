# probe23b: inheritance without mixed list
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

d = Dog("Rex")
print(d.name)
print(d.speak())

# Call the parent method via a parent reference
a: Animal = Dog("Buddy")
print(a.name)
print(a.speak())
