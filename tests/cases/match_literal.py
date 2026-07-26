# expect:
# one two other
def f(x):
    match x:
        case 1:
            return 'one'
        case 2:
            return 'two'
        case _:
            return 'other'
print(f(1), f(2), f(9))
# asmpython (beta/3.14.0) MISMATCH: prints '5368737792 5368737796 5368737800\n' (wrong).
