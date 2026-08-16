# probes: an inherited classmethod receives the SUBclass
# expect:
# Base
# Child
class Base:
    @classmethod
    def kind(cls):
        return cls.__name__


class Child(Base):
    pass


print(Base.kind())
print(Child.kind())
