# probes: an f-string may read an attribute
# expect:
# 5
class Point:
    def __init__(self):
        self.x = 5


print(f"{Point().x}")
