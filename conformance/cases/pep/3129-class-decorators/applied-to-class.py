# tier: spec
# ref: reference/compound_stmts.html#class-definitions
# expect:
# True
# C
def tag(cls):
    cls.tagged = True
    return cls

@tag
class C:
    pass

print(C.tagged)
print(C.__name__)
