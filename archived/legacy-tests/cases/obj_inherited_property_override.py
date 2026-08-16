# probes: a subclass can override an inherited property
# expect:
# base
# child
class Base:
    @property
    def label(self):
        return "base"


class Child(Base):
    @property
    def label(self):
        return "child"


print(Base().label)
print(Child().label)
