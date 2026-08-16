# expect:
# True
# True
# False
# True

class Base:
    pass


class Child(Base):
    pass


class Other:
    pass


def check(candidate):
    print(issubclass(candidate, Base))


check(Base)
check(Child)
check(Other)
print(issubclass(Child, (Other, Base)))
