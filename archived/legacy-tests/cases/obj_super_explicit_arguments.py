# probes: the two-argument super() form works
# expect:
# child+base
class Base:
    def tag(self):
        return "base"


class Child(Base):
    def tag(self):
        return "child+" + super(Child, self).tag()


print(Child().tag())
