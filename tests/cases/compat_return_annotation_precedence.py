# guards: return_annotation_precedence_compat_fixes
# expect:
# vec1
class Vec3:
    def __init__(self, x):
        self.x = x

    def show(self):
        return "vec" + str(self.x)


class Owner:
    def __init__(self):
        self._v = Vec3(1)

    @property
    def position(self) -> Vec3:
        return self._v


o = Owner()
print(o.position.show())
