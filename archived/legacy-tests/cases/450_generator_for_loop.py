# expect:
# 6
# 20
# 6
# 10
# 20
# 30

def gen_range(n: int):
    for i in range(n):
        yield i

def gen_transform(n: int):
    for i in range(n):
        yield i * 2

def gen_with_body(n: int):
    for i in range(n):
        x: int = i + 1
        yield x

def gen_list_iter(xs: list):
    for x in xs:
        yield x

result: int = 0
for v in gen_range(4):
    result = result + v
print(result)

result2: int = 0
for v in gen_transform(5):
    result2 = result2 + v
print(result2)

result3: int = 0
for v in gen_with_body(3):
    result3 = result3 + v
print(result3)

for v in gen_list_iter([10, 20, 30]):
    print(v)
