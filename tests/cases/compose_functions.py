# expect:
# 11
def compose(f, g):
    return lambda x: f(g(x))
inc = lambda x: x + 1
double = lambda x: x * 2
h = compose(inc, double)
print(h(5))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'f'
