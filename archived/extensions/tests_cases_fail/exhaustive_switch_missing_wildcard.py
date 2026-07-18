# ext: exhaustive_switch
# expect-error: does not cover every case

def describe(n: int) -> str:
    match n:
        case 1:
            return "one"
        case 2:
            return "two"
    return "unreached"

print(describe(2))
