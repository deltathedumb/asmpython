# expect:
# [3, 2, 1]
class R:
    def __init__(self, data):
        self.data = data
    def __reversed__(self):
        return reversed(self.data)
print(list(reversed(R([1, 2, 3]))))
# asmpython (beta/3.14.0) rejects at compile: [E022] reversed() argument must be a list, tuple, or str
