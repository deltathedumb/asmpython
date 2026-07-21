# expect:
# 1

class StringRegistry:
    def label(self):
        return "somnia.Scene"


class NumericRegistry:
    def label(self):
        return 7


REGISTRY = StringRegistry()


class Object:
    @property
    def type_name(self):
        return REGISTRY.label()


print(Object().type_name.startswith("somnia."))
