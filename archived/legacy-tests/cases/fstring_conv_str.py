# expect:
# S R
class P:
    def __str__(self):
        return 'S'
    def __repr__(self):
        return 'R'
print(f'{P()!s} {P()!r}')
# asmpython (beta/3.14.0) rejects at compile: asmpython: undefined symbol 'P____repr__' has no known DLL (add it to pe_linker._DLL_FOR_SYMBOL if it's a real import)
