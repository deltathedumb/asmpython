# expect:
# def
class C:
    pass


print(getattr(C(), 'x', 'def'))
# getattr(obj, name, default) returns garbage instead of the default under asmpython.
