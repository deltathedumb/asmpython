# expect-error: cannot unpack non-iterable Pair object
class Pair:
    def __init__(self, a: int, b: int):
        self.a = a
        self.b = b

def make() -> list[Pair]:
    out: list[Pair] = []
    out.append(Pair(1, 2))
    return out

for x, y in make():
    print(x, y)
