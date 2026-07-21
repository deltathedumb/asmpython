# expect:
# 1
# 1
# 0
# 1

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
