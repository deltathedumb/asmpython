# probes: a classmethod receives the class
# expect:
# Widget
# Widget
class Widget:
    @classmethod
    def kind(cls):
        return cls.__name__


print(Widget.kind())
print(Widget().kind())
