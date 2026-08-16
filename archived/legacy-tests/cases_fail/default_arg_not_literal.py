# expect-error: undefined variable 'foo'
def f(x=foo):
    return x


f()
