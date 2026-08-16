# guards: field_flow_compat_fixes
# expect:
# 3,4
# hello ada
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def render(self):
        return str(self.x) + "," + str(self.y)


class Named:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "hello " + self.name


print(Point(3, 4).render())
print(Named("ada").greet())
