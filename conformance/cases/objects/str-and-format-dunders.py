# tier: spec
# ref: reference/datamodel.html#object.__format__
# expect:
# STR REPR
# FMT:-
# FMT:>10
# FMT:-
# REPR
class C:
    def __str__(self):
        return "STR"
    def __repr__(self):
        return "REPR"
    def __format__(self, spec):
        return "FMT:" + (spec or "-")

c = C()
print(str(c), repr(c))
print(f"{c}")
print(f"{c:>10}")
print("{}".format(c))
print(f"{c!r}")
