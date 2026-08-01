# probes: a decorator applies to a method
# expect:
# 8
def double(fn):
    def wrapper(self, value):
        return fn(self, value) * 2

    return wrapper


class Calc:
    @double
    def add_one(self, value):
        return value + 1


print(Calc().add_one(3))
