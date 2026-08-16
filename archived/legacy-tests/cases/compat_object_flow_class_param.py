# guards: object_flow_compat_fixes
# expect:
# world
# engine
class World:
    def name(self):
        return "world"


class Engine:
    def name(self):
        return "engine"


class Services:
    def __init__(self):
        self._made = {}

    def get_service(self, kind):
        return kind()


s = Services()
print(s.get_service(World).name())
print(s.get_service(Engine).name())
