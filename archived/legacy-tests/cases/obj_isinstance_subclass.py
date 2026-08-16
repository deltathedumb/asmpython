# probes: isinstance accepts an instance of a subclass
# expect:
# True
# False
class Base:
    pass


class Child(Base):
    pass


print(isinstance(Child(), Base))
print(isinstance(Base(), Child))
