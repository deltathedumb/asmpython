# expect:
# 8
class Calculator:
    def add(self, a, b):
        return a + b
    def sub(self, a, b):
        return a - b
    def run(self, op, a, b):
        table = {'add': self.add, 'sub': self.sub}
        return table[op](a, b)
c = Calculator()
print(c.run('add', 5, 3))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
