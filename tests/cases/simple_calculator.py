# expect:
# 10 24 1.5
def calc(a, op, b):
    if op == '+':
        return a + b
    elif op == '-':
        return a - b
    elif op == '*':
        return a * b
    elif op == '/':
        return a / b
    return None
print(calc(6, '+', 4), calc(6, '*', 4), calc(6, '/', 4))
# asmpython (beta/3.14.0) MISMATCH: prints '10 24 6.0\n' (wrong).
