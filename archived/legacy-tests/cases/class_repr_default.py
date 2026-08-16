# expect:
# [Point(1, 2), Point(3, 4)]
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
    def __repr__(self):
        return 'Point(' + str(self.x) + ', ' + str(self.y) + ')'
print([Point(1, 2), Point(3, 4)])
# asmpython (beta/3.14.0) MISMATCH: prints '[9147584, 9147952]\n' (wrong).
