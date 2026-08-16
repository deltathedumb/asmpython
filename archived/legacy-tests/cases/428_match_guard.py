# expect:
# negative
# zero
# positive even
# positive odd
# 10
# 12
# -7
# 36
# unknown: add

def classify(n: int) -> str:
    match n:
        case x if x < 0:
            return "negative"
        case 0:
            return "zero"
        case x if x % 2 == 0:
            return "positive even"
        case _:
            return "positive odd"

print(classify(-5))
print(classify(0))
print(classify(4))
print(classify(7))

def process(cmd: str, val: int) -> str:
    match cmd:
        case "double":
            return str(val * 2)
        case "triple":
            return str(val * 3)
        case "negate":
            return str(-val)
        case "square":
            return str(val * val)
        case _:
            return "unknown: " + cmd

print(process("double", 5))
print(process("triple", 4))
print(process("negate", 7))
print(process("square", 6))
print(process("add", 1))
