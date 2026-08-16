# probes: a decorator replaces the function binding
# expect:
# HI ADA
def shout(fn):
    def wrapper(value):
        return fn(value).upper()

    return wrapper


@shout
def greet(name):
    return "hi " + name


print(greet("ada"))
