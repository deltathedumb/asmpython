# expect:
# 8
# 5
# 3
# hi


class Bag:
    def __init__(self) -> None:
        self._d: dict = {}

    def add(self, k: str, n: int) -> None:
        self._d[k] = n

    def get(self, k: str) -> int:
        return self._d[k]

    def total(self) -> int:
        t = 0
        for k in self._d:
            t = t + self._d[k]
        return t


d: dict = {}
d["a"] = 5
print(d["a"] + 3)

b = Bag()
b.add("x", 5)
print(b.get("x"))
b.add("y", -2)
print(b.total())

names: dict = {}
names["greeting"] = "hi"
g: str = names["greeting"]
print(g)
