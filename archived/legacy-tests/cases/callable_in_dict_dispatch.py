# expect:
# 7 7
ops = {'+': lambda a, b: a + b, '-': lambda a, b: a - b}
def calc(op, a, b):
    return ops[op](a, b)
print(calc('+', 3, 4), calc('-', 10, 3))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method '__call__'
