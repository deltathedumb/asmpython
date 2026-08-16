# expect:
# True
# False
# True
# False
# True
# False

def is_even(n: int) -> bool:
    return n % 2 == 0

print(is_even(4))
print(is_even(3))

class Checker:
    def ok(self, x: int) -> bool:
        return x > 0
    def is_empty(self, xs: list[int]) -> bool:
        return len(xs) == 0

c = Checker()
print(c.ok(5))
print(c.ok(-1))
xs: list[int] = []
print(c.is_empty(xs))
xs.append(1)
print(c.is_empty(xs))
