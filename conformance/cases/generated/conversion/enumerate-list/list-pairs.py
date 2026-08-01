# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# [(0, ('a', 1)), (1, ('b', 2))]
src = [('a', 1), ('b', 2)]
try:
    print(list(enumerate(src)))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
