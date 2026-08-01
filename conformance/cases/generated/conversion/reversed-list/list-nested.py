# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# [[3, 4], [1, 2]]
src = [[1, 2], [3, 4]]
try:
    print(list(reversed(list(src))))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
