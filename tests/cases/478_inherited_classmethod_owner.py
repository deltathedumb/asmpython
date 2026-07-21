# expect:
# 1
# 0
# 9

class Base:
    @classmethod
    def is_child(cls):
        return cls is Child

    @staticmethod
    def fixed():
        return 9


class Child(Base):
    pass


print(Child.is_child())
print(Base.is_child())
print(Child.fixed())
