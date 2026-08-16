# expect:
# 7.0
# 11.0

def add(x: float, y: float) -> float:
    return x + y

def mix(a: int, b: float, c: int, d: float) -> float:
    return a + b + c + d

print(add(3.0, 4.0))
print(mix(1, 2.5, 3, 4.5))
