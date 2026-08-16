# probes: for falls back to __getitem__ when __iter__ is absent
# expect:
# [10, 9, 8]
class Countdown:
    def __getitem__(self, index):
        if index > 2:
            raise IndexError(index)
        return 10 - index


print([v for v in Countdown()])
