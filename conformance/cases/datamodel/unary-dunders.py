# tier: spec
# ref: reference/datamodel.html#object.__neg__
# expect:
# neg pos abs
# -5 5 5 5
# -6 0
class U:
    def __neg__(self): return "neg"
    def __pos__(self): return "pos"
    def __abs__(self): return "abs"

u = U()
print(-u, +u, abs(u))
print(-5, +5, abs(-5), abs(5))
print(~5, ~-1)
