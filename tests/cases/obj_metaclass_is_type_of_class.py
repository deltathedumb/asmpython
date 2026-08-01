# probes: type(cls) is its metaclass
# expect:
# Meta
# Widget
class Meta(type):
    pass


class Widget(metaclass=Meta):
    pass


print(type(Widget).__name__)
print(type(Widget()).__name__)
