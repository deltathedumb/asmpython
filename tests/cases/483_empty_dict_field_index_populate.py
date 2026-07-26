# expect:
# 2
# 3
# 2


class Bag:
    def __init__(self) -> None:
        self._d: dict[str, int] = {}

    def add(self, k: str) -> None:
        if k in self._d:
            self._d[k] = self._d[k] + 1
        else:
            self._d[k] = 1

    def num_keys(self) -> int:
        n = 0
        for k in self._d:
            n = n + 1
        return n

    def total(self) -> int:
        t = 0
        for k in self._d:
            t = t + self._d[k]
        return t


b = Bag()
b.add("a")
b.add("a")
b.add("b")
print(b.num_keys())
print(b.total())
print(b._d["a"])
