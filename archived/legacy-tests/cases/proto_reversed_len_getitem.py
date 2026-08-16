# probes: reversed() falls back to __len__ plus __getitem__
# expect:
# [20, 10, 0]
class Seq:
    def __len__(self):
        return 3

    def __getitem__(self, index):
        return index * 10


print(list(reversed(Seq())))
