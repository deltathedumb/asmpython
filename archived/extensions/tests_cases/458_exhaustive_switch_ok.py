# ext: exhaustive_switch
# expect:
# two
# other

def describe(n: int) -> str:
    match n:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"

print(describe(2))
print(describe(99))
