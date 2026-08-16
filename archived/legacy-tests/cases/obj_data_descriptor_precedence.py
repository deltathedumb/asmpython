# probes: a data descriptor beats the instance dict
# expect:
# instance
# descriptor
class Data:
    def __get__(self, obj, owner):
        return "descriptor"

    def __set__(self, obj, value):
        obj.__dict__["field"] = value


class Holder:
    field = Data()


h = Holder()
h.field = "instance"
print(h.__dict__["field"])
print(h.field)
