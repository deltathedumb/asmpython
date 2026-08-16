# probes: any() consumes a generator lazily
# expect:
# yielded 1
# yielded 2
# True
def values():
    for n in [1, 2, 3]:
        print("yielded " + str(n))
        yield n > 1


print(any(values()))
