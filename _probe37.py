# probe37: advanced patterns - generators (unsupported?), match statement (3.10+)
# match statement
def classify(n: int) -> str:
    match n:
        case 0:
            return "zero"
        case 1 | 2 | 3:
            return "small"
        case _:
            return "big"

print(classify(0))   # zero
print(classify(2))   # small
print(classify(10))  # big

# match with guard
def grade(score: int) -> str:
    match score:
        case s if s >= 90:
            return "A"
        case s if s >= 80:
            return "B"
        case s if s >= 70:
            return "C"
        case _:
            return "F"

print(grade(95))   # A
print(grade(85))   # B
print(grade(65))   # F
