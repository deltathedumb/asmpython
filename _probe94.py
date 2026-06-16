# probe94: class methods, static methods, class variables
class Counter:
    _count: int = 0   # class variable

    def __init__(self, name: str) -> None:
        self.name = name
        Counter._count = Counter._count + 1

    @classmethod
    def get_count(cls) -> int:
        return cls._count

    @staticmethod
    def reset() -> None:
        Counter._count = 0

    def __del__(self) -> None:
        pass  # avoid errors

c1 = Counter("first")
c2 = Counter("second")
c3 = Counter("third")

print(Counter.get_count())   # 3
Counter.reset()
print(Counter.get_count())   # 0

# Class with __slots__-like pattern
class Pair:
    def __init__(self, left: int, right: int) -> None:
        self.left = left
        self.right = right

    def swap(self) -> "Pair":
        return Pair(self.right, self.left)

    def __str__(self) -> str:
        return "(" + str(self.left) + ", " + str(self.right) + ")"

p = Pair(1, 2)
print(str(p))         # (1, 2)
q = p.swap()
print(str(q))         # (2, 1)
print(str(p))         # (1, 2) unchanged
