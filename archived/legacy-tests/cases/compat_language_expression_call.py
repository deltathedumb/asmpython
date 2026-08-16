# guards: language_compat_fixes
# expect:
# 15
# 3
class Adder:
    def __init__(self, base):
        self.base = base

    def __call__(self, n):
        return self.base + n


def get_adder(base):
    return Adder(base)


print(get_adder(10)(5))

table = {"a": Adder(1)}
print(table["a"](2))
