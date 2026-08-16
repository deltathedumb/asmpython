# expect:
# True
class Meta(type):
    def __new__(mcs, name, bases, ns):
        ns['created'] = True
        return super().__new__(mcs, name, bases, ns)
class C(metaclass=Meta):
    pass
print(C.created)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
