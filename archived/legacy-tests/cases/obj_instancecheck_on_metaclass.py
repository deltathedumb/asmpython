# probes: __instancecheck__ overrides isinstance
# expect:
# True
# True
class AlwaysMeta(type):
    def __instancecheck__(cls, obj):
        return True


class Anything(metaclass=AlwaysMeta):
    pass


print(isinstance("a string", Anything))
print(isinstance(42, Anything))
