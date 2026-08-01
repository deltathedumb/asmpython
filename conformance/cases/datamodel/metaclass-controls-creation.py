# tier: spec
# ref: reference/datamodel.html#metaclasses
# expect:
# [('new', 'C', [('flavour', 'x')]), ('init', 'C')]
# Meta
log = []

class Meta(type):
    def __new__(mcls, name, bases, ns, **kw):
        log.append(("new", name, sorted(kw.items())))
        return super().__new__(mcls, name, bases, ns)
    def __init__(cls, name, bases, ns, **kw):
        log.append(("init", name))
        super().__init__(name, bases, ns)

class C(metaclass=Meta, flavour="x"):
    pass

print(log)
print(type(C).__name__)
