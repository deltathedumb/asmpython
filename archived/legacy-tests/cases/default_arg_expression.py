# expect:
# 20
def f(x, n=2 + 3):
    return x * n
print(f(4))
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP ',', got OP '+'
