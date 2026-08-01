# guards: dynamic_classvar_compat_fixes
# expect:
# label=base
# label=child
# label=child
class Base:
    label = "base"

    @classmethod
    def describe(cls):
        return "label=" + cls.label


class Child(Base):
    label = "child"


class Grandchild(Child):
    pass


print(Base.describe())
print(Child.describe())
print(Grandchild.describe())
