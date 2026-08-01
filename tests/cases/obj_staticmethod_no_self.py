# probes: a staticmethod takes no implicit argument
# expect:
# 5
# 9
class MathBox:
    @staticmethod
    def add(a, b):
        return a + b


print(MathBox.add(2, 3))
print(MathBox().add(4, 5))
