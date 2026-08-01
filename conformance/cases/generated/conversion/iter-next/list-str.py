# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# c
src = ['c', 'a', 'b']
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
