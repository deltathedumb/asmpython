# probes: a field may read an attribute
# expect:
# 3
class Point:
    def __init__(self):
        self.x = 3


print("{0.x}".format(Point()))
