# tier: spec
# ref: reference/datamodel.html#object.__int__
# expect:
# 7 7.5 (1+2j)
# round:None round:2
# False
class N:
    def __int__(self): return 7
    def __float__(self): return 7.5
    def __complex__(self): return complex(1, 2)
    def __round__(self, nd=None): return "round:" + str(nd)
    def __bool__(self): return False

n = N()
print(int(n), float(n), complex(n))
print(round(n), round(n, 2))
print(bool(n))
