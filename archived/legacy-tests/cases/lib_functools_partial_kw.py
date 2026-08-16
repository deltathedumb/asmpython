# expect:
# Hello, World
from functools import partial
def greet(greeting, name):
    return greeting + ', ' + name
hello = partial(greet, 'Hello')
print(hello('World'))
# asmpython (beta/3.14.0) rejects at compile: [E012] unsupported operand type for +: int + str
