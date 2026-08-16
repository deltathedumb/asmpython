# probes: isinstance sees the real runtime kind
# expect:
# int
# float
# str
# bool
# other
def kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return "other"


print(kind(1))
print(kind(1.5))
print(kind("s"))
print(kind(True))
print(kind([1]))
