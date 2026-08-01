# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# 3
src = [1, 'a', 2.5]
try:
    print(len(list(src)))
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
