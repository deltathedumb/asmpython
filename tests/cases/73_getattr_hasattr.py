# expect:
# kind=int
# missing=none
# present=1
# absent=0
# 7
# vararg=42
# membership ok
# not a keyword
class Node:
    def __init__(self, kind: str):
        self.kind = kind


def classify(op: str) -> str:
    # `x in (a, b, ...)` over a tuple literal — homogeneous str membership.
    if op in ("+", "-", "*", "/"):
        return "arith"
    if op in ("&", "|", "^"):
        return "bitwise"
    return "other"


n = Node("int")
# getattr with a literal name; field is present.
print("kind=" + getattr(n, "kind", "?"))
# getattr with a default when the attribute was never set on the instance.
print("missing=" + getattr(n, "weight", "none"))
# hasattr reflects whether the field exists in the instance dict.
print("present=" + str(hasattr(n, "kind")))
print("absent=" + str(hasattr(n, "weight")))

# getattr default can be a computed (non-literal) value, parked across the
# object evaluation.
print(getattr(n, "rank", 3 + 4))

# A getattr that was set later is read back.
n.rank = 42
print("vararg=" + str(getattr(n, "rank", 0)))

if "/" in ("+", "-", "*", "/") and "%" not in ("+", "-"):
    print("membership ok")

keywords = ("def", "class", "return")
name = "kind"
if name not in keywords:
    print("not a keyword")
