# ext: enum
# expect:
# 0
# 1
# 2
# match

enum Color:
    RED
    GREEN
    BLUE

print(Color.RED)
print(Color.GREEN)
print(Color.BLUE)

c = Color.GREEN
if c == Color.GREEN:
    print("match")
