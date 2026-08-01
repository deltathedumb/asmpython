# probes: a custom iterable works with zip
# expect:
# [(1, 'a'), (2, 'b')]
class Letters:
    def __iter__(self):
        return iter(["a", "b"])


print(list(zip([1, 2], Letters())))
