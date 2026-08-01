# tier: spec
# ref: reference/executionmodel.html#resolution-of-names
# expect:
# outer
# ('inner', 'module')
x = "module"

def outer():
    x = "outer"
    def middle():
        def inner():
            return x
        return inner()
    return middle()

print(outer())

def shadowed():
    def inner():
        x = "inner"
        return x
    return inner(), x

print(shadowed())
