# expect:
# HELLO
# 5
# 3
# 6
# pt=9
def shout(s: str) -> str:
    return s.upper()

def total(xs: list) -> int:
    acc = 0
    for v in xs:
        acc = acc + v
    return acc

def first_char(s: str) -> str:
    return s[0]

class Point:
    def __init__(self, x):
        self.x = x

def get_x(p: Point) -> int:
    return p.x

print(shout("hello"))
print(len("hello"))
nums = [1, 2]
print(first_char("3"))
ys = [1, 2, 3]
print(total(ys))
pt = Point(9)
print("pt=" + str(get_x(pt)))
