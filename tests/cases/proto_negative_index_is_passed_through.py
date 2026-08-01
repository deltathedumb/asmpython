# probes: a negative index reaches __getitem__ unchanged
# expect:
# -1
class Echo:
    def __getitem__(self, index):
        return index


print(Echo()[-1])
