# expect:
# 1
# 2
# 1

class ParentType:
    value = 1

    @classmethod
    def get_value(cls):
        return cls.value


class OverrideType(ParentType):
    value = 2


class InheritType(ParentType):
    pass


print(ParentType.get_value())
print(OverrideType.get_value())
print(InheritType.get_value())
