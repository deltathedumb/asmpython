# tier: spec
# ref: library/functions.html#staticmethod
# expect:
# static:1 static:1
# C:1 C:1
# staticmethod
# classmethod
class C:
    @staticmethod
    def s(v):
        return "static:" + str(v)
    @classmethod
    def c(cls, v):
        return cls.__name__ + ":" + str(v)

print(C.s(1), C().s(1))
print(C.c(1), C().c(1))
print(type(C.__dict__["s"]).__name__)
print(type(C.__dict__["c"]).__name__)
