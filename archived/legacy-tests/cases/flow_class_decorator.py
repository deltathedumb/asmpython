# probes: a class decorator replaces the class binding
# expect:
# tagged
def tagged(cls):
    cls.tag = "tagged"
    return cls


@tagged
class Widget:
    pass


print(Widget.tag)
