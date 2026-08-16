# expect:
# Hello, Alice!
# Hi, Bob!
# Yo, Carol!
# Hey, Dan!
# >> Hello, Eve!
# >> Hi, Frank!
# >> Yo, Grace!
# >> Hey, Heidi!

def greet(name: str, greeting: str = "Hello") -> str:
    return greeting + ", " + name + "!"

print(greet("Alice"))
print(greet("Bob", greeting="Hi"))
print(greet(name="Carol", greeting="Yo"))
print(greet(greeting="Hey", name="Dan"))


class Greeter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def greet(self, name: str, greeting: str = "Hello") -> str:
        return self.prefix + greeting + ", " + name + "!"


g = Greeter(">> ")
print(g.greet("Eve"))
print(g.greet("Frank", greeting="Hi"))
print(g.greet(name="Grace", greeting="Yo"))
print(g.greet(greeting="Hey", name="Heidi"))
