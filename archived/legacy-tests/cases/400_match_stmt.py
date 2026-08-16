# expect:
# one
# two
# other

def describe(x: int) -> str:
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"

print(describe(1))
print(describe(2))
print(describe(99))
