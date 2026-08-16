# expect:
# hi bob
def call(func, *args):
    return lambda: func(*args)
def greet(name):
    return 'hi ' + name
c = call(greet, 'bob')
print(c())
# asmpython (beta/3.14.0) rejects at compile: [E012] unsupported operand type for +: str + int
