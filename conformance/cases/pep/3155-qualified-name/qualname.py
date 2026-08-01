# tier: spec
# ref: peps.python.org/pep-3155/
# expect:
# top
# C
# C.m
# top
def top():
    pass

class C:
    def m(self):
        pass

print(top.__qualname__)
print(C.__qualname__)
print(C.m.__qualname__)
print(top.__name__)
