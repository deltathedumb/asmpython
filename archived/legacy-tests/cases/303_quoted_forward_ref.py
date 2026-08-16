# expect:
# 6
# 4
# 6

from __future__ import annotations


class A:
    def __init__(self, val: int) -> None:
        self.val = val

    def make_b(self) -> "B":
        return B(self.val + 1)

    def parent(self) -> "A":
        return A(self.val - 1)


class B:
    def __init__(self, val: int) -> None:
        self.val = val


a = A(5)
b = a.make_b()
print(b.val)

p = a.parent()
print(p.val)


def make_list() -> "list[int]":
    return [1, 2, 3]


nums = make_list()
print(nums[0] + nums[1] + nums[2])
