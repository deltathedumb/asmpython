# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# ['a', 'b']
src = {'b': 2, 'a': 1}
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
