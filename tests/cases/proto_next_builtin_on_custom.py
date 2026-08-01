# probes: next() drives a custom __next__
# expect:
# only
# default
class Once:
    def __init__(self):
        self.done = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.done:
            raise StopIteration
        self.done = True
        return "only"


it = iter(Once())
print(next(it))
print(next(it, "default"))
