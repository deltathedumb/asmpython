# probes: a subclass inherits a property
# expect:
# base-label
class Base:
    @property
    def label(self):
        return "base-label"


class Child(Base):
    pass


print(Child().label)
