# expect:
# 7

class Descriptor:
    def __set_name__(self, owner, name):
        self.name = name

    def __set__(self, instance, value):
        instance.values[self.name] = value


class Box:
    marker = Descriptor()

    def __init__(self):
        self.values = {}


box = Box()
box.marker = 7
print(box.values["marker"])
