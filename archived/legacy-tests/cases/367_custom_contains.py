# expect:
# 1
# 0
# 0

class Bag:
    def __init__(self) -> None:
        self._items: list[int] = []

    def add(self, x: int) -> None:
        self._items.append(x)

    def __contains__(self, x: int) -> int:
        i: int = 0
        while i < len(self._items):
            if self._items[i] == x:
                return 1
            i = i + 1
        return 0

b: Bag = Bag()
b.add(10)
b.add(20)
r1: int = 1 if 10 in b else 0
r2: int = 1 if 30 in b else 0
r3: int = 1 if 20 not in b else 0
print(r1)
print(r2)
print(r3)
