# ext: exhaustive_switch
# expect-error: does not cover every case

def describe(n: int) -> str:
    match n:
        case 1:
            return "one"
        case _ if n > 10:
            return "big"
    return "unreached"

print(describe(2))
