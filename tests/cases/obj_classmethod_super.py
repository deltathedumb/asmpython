# probes: super() works inside a classmethod
# expect:
# child+base
class Base:
    @classmethod
    def tag(cls):
        return "base"


class Child(Base):
    @classmethod
    def tag(cls):
        return "child+" + super().tag()


print(Child.tag())
