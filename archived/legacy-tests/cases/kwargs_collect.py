# expect:
# [('x', 1), ('y', 2), ('z', 3)]
def f(**opts):
    return sorted(opts.items())
print(f(x=1, y=2, z=3))
# asmpython (beta/3.14.0) MISMATCH: prints '[9606496, 9606560, 9606624]\n' (wrong).
