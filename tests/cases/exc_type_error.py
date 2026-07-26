# expect:
# type error
try:
    x = 'a' + 1
except TypeError:
    print('type error')
# asmpython (beta/3.14.0) rejects at compile: [E012] unsupported operand type for +: str + int
