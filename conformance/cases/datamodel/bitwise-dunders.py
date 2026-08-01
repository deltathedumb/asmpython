# tier: spec
# ref: reference/datamodel.html#emulating-numeric-types
# expect:
# and or xor lshift rshift invert
class B:
    def __and__(self, o): return "and"
    def __or__(self, o): return "or"
    def __xor__(self, o): return "xor"
    def __lshift__(self, o): return "lshift"
    def __rshift__(self, o): return "rshift"
    def __invert__(self): return "invert"

b = B()
print(b & 1, b | 1, b ^ 1, b << 1, b >> 1, ~b)
