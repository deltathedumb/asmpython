# expect:
# 10
class Handler:
    def __init__(self, fn):
        self.fn = fn
    def run(self, x):
        return self.fn(x)
h = Handler(lambda x: x * 2)
print(h.run(5))
# asmpython (beta/3.14.0) rejects at compile: [E113] Handler has no method 'fn'
