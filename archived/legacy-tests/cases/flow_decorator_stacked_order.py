# probes: stacked decorators apply bottom-up
# expect:
# outer(inner(base))
def outer(fn):
    def wrapper():
        return "outer(" + fn() + ")"

    return wrapper


def inner(fn):
    def wrapper():
        return "inner(" + fn() + ")"

    return wrapper


@outer
@inner
def base():
    return "base"


print(base())
