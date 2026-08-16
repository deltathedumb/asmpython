# probes: __set_name__ receives the attribute name
# expect:
# first
# second
class Named:
    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner):
        return self.name


class Holder:
    first = Named()
    second = Named()


h = Holder()
print(h.first)
print(h.second)
