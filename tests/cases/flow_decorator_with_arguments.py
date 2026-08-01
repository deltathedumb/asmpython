# probes: a decorator factory takes its own arguments
# expect:
# ababab
def repeat(times):
    def decorate(fn):
        def wrapper(value):
            out = ""
            for _ in range(times):
                out = out + fn(value)
            return out

        return wrapper

    return decorate


@repeat(3)
def dot(value):
    return value


print(dot("ab"))
