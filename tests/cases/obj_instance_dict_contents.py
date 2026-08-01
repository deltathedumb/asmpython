# probes: vars() exposes the instance dict
# expect:
# {'x': 1, 'y': 2}
class Point:
    def __init__(self):
        self.x = 1
        self.y = 2


print(vars(Point()))
