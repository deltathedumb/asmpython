# expect:
# small big
def f(x):
    match x:
        case 1 | 2 | 3:
            return 'small'
        case _:
            return 'big'
print(f(2), f(10))
# asmpython (beta/3.14.0) MISMATCH: prints '5368737792 5368737798\n' (wrong).
