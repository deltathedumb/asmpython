# expect:
# 5
class Loop:
    def __init__(self, cond):
        self.cond = cond
        self.tick = 0
    def run(self):
        while self.cond(self.tick):
            self.tick += 1
        return self.tick
l = Loop(lambda t: t < 5)
print(l.run())
# asmpython (beta/3.14.0) rejects at compile: [E113] Loop has no method 'cond'
