# probes: a case guard further constrains a pattern
# expect:
# negative
# zero
# positive
def classify(n):
    match n:
        case v if v < 0:
            return "negative"
        case 0:
            return "zero"
        case _:
            return "positive"


print(classify(-1))
print(classify(0))
print(classify(5))
