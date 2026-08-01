# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# [3, 1, 2]
src = (3, 1, 2)
try:
    print(list(src))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
