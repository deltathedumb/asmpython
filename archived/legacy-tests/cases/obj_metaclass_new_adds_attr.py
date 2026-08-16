# probes: a metaclass __new__ can edit the namespace
# expect:
# tagged-Widget
class Tagging(type):
    def __new__(mcls, name, bases, namespace):
        namespace["tag"] = "tagged-" + name
        return super().__new__(mcls, name, bases, namespace)


class Widget(metaclass=Tagging):
    pass


print(Widget.tag)
