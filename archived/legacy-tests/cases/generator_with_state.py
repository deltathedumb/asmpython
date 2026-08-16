# expect:
# 10 11 12
def counter(start):
    n = start
    while True:
        yield n
        n += 1
g = counter(10)
print(next(g), next(g), next(g))
# asmpython (beta/3.14.0) rejects at compile: asmpython: undefined symbol '_genobj_counter____next__' has no known DLL (add it to pe_linker._DLL_FOR_SYMBOL if it's a real import)
