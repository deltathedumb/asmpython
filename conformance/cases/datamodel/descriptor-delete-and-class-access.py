# tier: spec
# ref: reference/datamodel.html#invoking-descriptors
# expect:
# value
# value
# [('get', True, 'C'), ('get', False, 'C'), ('set', 1), 'delete']
log = []

class D:
    def __get__(self, obj, owner=None):
        log.append(("get", obj is None, owner.__name__))
        return "value"
    def __set__(self, obj, v):
        log.append(("set", v))
    def __delete__(self, obj):
        log.append("delete")

class C:
    d = D()

print(C.d)
c = C()
print(c.d)
c.d = 1
del c.d
print(log)
