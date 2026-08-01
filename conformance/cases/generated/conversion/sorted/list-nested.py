# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# [[1, 2], [3, 4]]
src = [[1, 2], [3, 4]]
try:
    print(sorted(src, key=repr))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
