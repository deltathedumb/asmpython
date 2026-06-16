# expect:
# True
# True
# False
# False
# True
# True

# isinstance(x, Cls): checks the instance's runtime class id against Cls and its
# subclasses. Instances are tagged with their class id at construction.

class Animal:
    def __init__(self, name):
        self.name = name


class Dog(Animal):
    def __init__(self, name):
        self.name = name


class Cat:
    def __init__(self):
        self.x = 1


d = Dog("rex")
c = Cat()
print(isinstance(d, Dog))      # 1
print(isinstance(d, Animal))   # 1 (Dog subclasses Animal)
print(isinstance(d, Cat))      # 0
print(isinstance(c, Animal))   # 0
print(isinstance(c, Cat))      # 1
print(isinstance(d, (Cat, Animal)))  # 1 (tuple of classes)
