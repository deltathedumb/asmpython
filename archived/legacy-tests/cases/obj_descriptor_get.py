# probes: __get__ is invoked on attribute read
# expect:
# descriptor-value
class Const:
    def __get__(self, obj, owner):
        return "descriptor-value"


class Holder:
    field = Const()


print(Holder().field)
