# probes: issubclass reports the class relation
# expect:
# True
# False
# True
class Base:
    pass


class Child(Base):
    pass


print(issubclass(Child, Base))
print(issubclass(Base, Child))
print(issubclass(Child, Child))
