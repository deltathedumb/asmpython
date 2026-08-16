# expect:
# 25C
class Temp:
    def __init__(self, c):
        self.c = c
    def __format__(self, spec):
        return str(self.c) + 'C'
print(f'{Temp(25)}')
# asmpython (beta/3.14.0) MISMATCH: prints '8950912\n' (wrong).
