# probe116: complex augmented assignments in various contexts
# Augmented on class attribute
class Counter:
    def __init__(self) -> None:
        self.count = 0
        self.total = 0

    def add(self, v: int) -> None:
        self.count += 1
        self.total += v

    def avg(self) -> int:
        if self.count == 0:
            return 0
        return self.total // self.count

c = Counter()
c.add(10)
c.add(20)
c.add(30)
print(c.count)   # 3
print(c.total)   # 60
print(c.avg())   # 20

# Augmented on list elements
xs = [1, 2, 3, 4, 5]
xs[0] += 10
xs[2] *= 3
xs[4] -= 1
print(xs)   # [11, 2, 9, 4, 4]

# Augmented on nested list
matrix = [[1, 2], [3, 4]]
matrix[0][1] += 10
print(matrix[0][1])   # 12

# String augmented assignment
s = "hello"
s += " world"
s += "!"
print(s)   # hello world!

# Augmented in loop
total: int = 0
for i in range(1, 6):
    total += i
print(total)   # 15
