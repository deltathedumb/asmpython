# expect:
# 0
# 3
# 13
# 0

from __future__ import annotations

class Counter:
    total: int = 0
    step: int = 1

    @classmethod
    def increment(cls) -> None:
        cls.total = cls.total + cls.step

    @classmethod
    def reset(cls) -> None:
        cls.total = 0

    @classmethod
    def get_total(cls) -> int:
        return cls.total

    @classmethod
    def set_step(cls, n: int) -> None:
        cls.step = n

print(Counter.get_total())
Counter.increment()
Counter.increment()
Counter.increment()
print(Counter.get_total())

Counter.set_step(10)
Counter.increment()
print(Counter.get_total())

Counter.reset()
print(Counter.get_total())
