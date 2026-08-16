# probes: a non-data descriptor loses to the instance dict
# expect:
# descriptor
# instance
class NonData:
    def __get__(self, obj, owner):
        return "descriptor"


class Holder:
    field = NonData()


h = Holder()
print(h.field)
h.__dict__["field"] = "instance"
print(h.field)
