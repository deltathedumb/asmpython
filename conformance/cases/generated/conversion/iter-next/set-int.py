# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# 1
src = {3, 1, 2}
try:
    it = iter(src)
    print(next(it))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
