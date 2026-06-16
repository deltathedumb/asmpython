# probe130: complex match statement patterns
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

print(process("double", 5))    # 10
print(process("triple", 4))    # 12
print(process("negate", 7))    # -7
print(process("square", 6))    # 36
print(process("add", 1))       # unknown: add

# Match with guard
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

print(classify(-5))   # negative
print(classify(0))    # zero
print(classify(4))    # positive even
print(classify(7))    # positive odd

# Match on type-based pattern (string vs int-like)
def describe(val: str) -> str:
    match val:
        case "":
            return "empty"
        case "true" | "yes" | "on":
            return "truthy"
        case "false" | "no" | "off":
            return "falsy"
        case _:
            return "other: " + val

print(describe(""))       # empty
print(describe("yes"))    # truthy
print(describe("no"))     # falsy
print(describe("hello"))  # other: hello
