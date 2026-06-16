# probe82: match statement (Python 3.10+)
def describe(x: int) -> str:
    match x:
        case 0:
            return "zero"
        case 1:
            return "one"
        case 2 | 3:
            return "two or three"
        case _:
            return "other"

print(describe(0))   # zero
print(describe(1))   # one
print(describe(2))   # two or three
print(describe(3))   # two or three
print(describe(99))  # other

# match with string
def classify(s: str) -> str:
    match s:
        case "red" | "blue" | "green":
            return "color"
        case "cat" | "dog":
            return "animal"
        case _:
            return "unknown"

print(classify("red"))     # color
print(classify("dog"))     # animal
print(classify("hello"))   # unknown
