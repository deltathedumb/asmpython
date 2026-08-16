# guards: inherited_classmethod_compat_fixes
# expect:
# provider:a
# scene:b
# 2
class Provider:
    kind = "provider"

    @classmethod
    def supports(cls, what):
        return cls.kind + ":" + what

    @staticmethod
    def version():
        return 2


class Scene(Provider):
    kind = "scene"


print(Provider.supports("a"))
print(Scene.supports("b"))
print(Scene.version())
