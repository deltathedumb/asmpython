# probes: each __init__ in a diamond runs once
# expect:
# ['Base', 'Right', 'Left', 'Both']
class Base:
    def __init__(self):
        self.trail = ["Base"]


class Left(Base):
    def __init__(self):
        super().__init__()
        self.trail.append("Left")


class Right(Base):
    def __init__(self):
        super().__init__()
        self.trail.append("Right")


class Both(Left, Right):
    def __init__(self):
        super().__init__()
        self.trail.append("Both")


print(Both().trail)
