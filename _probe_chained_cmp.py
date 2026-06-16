# expect:
# True
# False
# True
# True
# 2

def classify(x: int) -> str:
    if 0 < x < 10:
        return "small"
    elif 10 <= x <= 100:
        return "medium"
    else:
        return "large"

print(0 < 5 < 10)
print(0 < 15 < 10)
print(1 <= 1 <= 5)
print(1 < 2 < 3 < 4)

xs = [1, 2, 3, 4, 5]
count: int = 0
for x in xs:
    if x > 1 and x < 4:
        count += 1
print(count)
