# isinstance and type checking
x = 42
print(isinstance(x, int))
print(isinstance(x, str))
print(isinstance("hello", str))
print(isinstance([1,2,3], list))

class Animal:
    pass

class Dog(Animal):
    pass

d = Dog()
print(isinstance(d, Dog))
print(isinstance(d, Animal))
