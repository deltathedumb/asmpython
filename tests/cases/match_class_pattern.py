# expect:
# o 3,4
class Pt:
    __match_args__ = ('x', 'y')
    def __init__(self, x, y):
        self.x = x
        self.y = y
def f(p):
    match p:
        case Pt(0, 0):
            return 'o'
        case Pt(x, y):
            return str(x) + ',' + str(y)
print(f(Pt(0, 0)), f(Pt(3, 4)))
# asmpython (beta/3.14.0) MISMATCH: prints '5368741902 8362288\n' (wrong).
