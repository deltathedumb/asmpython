# tier: spec
# ref: library/stdtypes.html#iterator-types
# expect:
# ['a', 'b', 'c']
# frozenset
src = 'cab'
try:
    print(sorted(frozenset(src), key=repr))
    print(type(frozenset(src)).__name__)
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
