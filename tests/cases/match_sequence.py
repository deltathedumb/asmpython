# expect:
# origin xaxis point
def f(pt):
    match pt:
        case [0, 0]:
            return 'origin'
        case [x, 0]:
            return 'xaxis'
        case [x, y]:
            return 'point'
print(f([0, 0]), f([5, 0]), f([1, 2]))
# asmpython (beta/3.14.0) MISMATCH: prints '5368737816 5368737823 5368737829\n' (wrong).
