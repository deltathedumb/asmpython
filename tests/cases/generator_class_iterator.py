# expect:
# [3, 2, 1]
class Countdown:
    def __init__(self, n):
        self.n = n
    def __iter__(self):
        while self.n > 0:
            yield self.n
            self.n -= 1
print(list(Countdown(3)))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
