# expect:
# 3
def repeat(n):
    def deco(f):
        def wrap(x):
            r = x
            for _ in range(n):
                r = f(r)
            return r
        return wrap
    return deco
@repeat(3)
def inc(x):
    return x + 1
print(inc(0))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'f'
