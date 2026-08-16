# guards: type_parameter_compat_fixes
# expect:
# world
# engine
# world
class World:
    def tag(self):
        return "world"


class Engine:
    def tag(self):
        return "engine"


class Container:
    def __init__(self):
        self.have = {}

    def ensure(self, service_type):
        found = self.have.get(service_type.__name__)
        if found is None:
            found = service_type()
            self.have[service_type.__name__] = found
        return found


c = Container()
print(c.ensure(World).tag())
print(c.ensure(Engine).tag())
print(c.ensure(World).tag())
