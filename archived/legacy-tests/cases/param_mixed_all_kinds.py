# expect:
# 16
def f(a, b=2, *args, c, **kwargs):
    return a + b + c + sum(args) + len(kwargs)
print(f(1, 2, 3, 4, c=5, x=6))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
