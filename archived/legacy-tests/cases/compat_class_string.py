# guards: class_string_compat_fixes
# expect:
# Alpha
# beta
class Alpha:
    pass


def name_of(value):
    if isinstance(value, type):
        return value.__name__
    return str(value)


print(name_of(Alpha))
print(name_of("beta"))
