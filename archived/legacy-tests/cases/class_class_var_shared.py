# expect:
# ['a', 'b']
class C:
    registry = []
    def reg(self, x):
        C.registry.append(x)
C().reg('a')
C().reg('b')
print(C.registry)
# asmpython (beta/3.14.0) MISMATCH: prints '[5368737811, 5368737813]\n' (wrong).
