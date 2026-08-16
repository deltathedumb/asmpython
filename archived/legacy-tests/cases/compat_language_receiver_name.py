# guards: language_compat_fixes
# expect:
# box(1)
# 2
# box(2)
class Box:
    def __init__(this, value):
        this.value = value

    def show(this):
        return "box(" + str(this.value) + ")"

    def bump(obj):
        obj.value = obj.value + 1
        return obj.value


b = Box(1)
print(b.show())
print(b.bump())
print(b.show())
