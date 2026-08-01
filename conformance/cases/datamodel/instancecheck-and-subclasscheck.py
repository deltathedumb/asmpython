# tier: spec
# ref: reference/datamodel.html#customizing-instance-and-subclass-checks
# expect:
# True
# True
class Meta(type):
    def __instancecheck__(cls, obj):
        return "quacks"
    def __subclasscheck__(cls, sub):
        return True

class Duck(metaclass=Meta):
    pass

print(isinstance(42, Duck))
print(issubclass(int, Duck))
