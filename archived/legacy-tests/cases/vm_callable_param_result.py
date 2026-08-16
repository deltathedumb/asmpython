# probes: a called-through parameter keeps its result kind
# expect:
# HI
# 42
def apply(fn, arg):
    return fn(arg)


def shout(s):
    return s.upper()


def double(n):
    return n * 2


print(apply(shout, "hi"))
print(apply(double, 21))
