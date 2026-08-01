# probes: super().__init__ runs the base initializer
# expect:
# ada
# 7
class Base:
    def __init__(self, name):
        self.name = name


class Child(Base):
    def __init__(self, name, extra):
        super().__init__(name)
        self.extra = extra


c = Child("ada", 7)
print(c.name)
print(c.extra)
