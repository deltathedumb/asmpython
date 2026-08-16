# expect:
# neg zero pos
def f(x):
    match x:
        case n if n < 0:
            return 'neg'
        case 0:
            return 'zero'
        case _:
            return 'pos'
print(f(-5), f(0), f(3))
# asmpython (beta/3.14.0) MISMATCH: prints '5368737792 5368737796 5368737801\n' (wrong).
