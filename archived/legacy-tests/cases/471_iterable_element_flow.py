# expect:
# 1
# 0
# 1

class Provider:
    realms = ("server", "client")

    @classmethod
    def supports(cls, realm):
        return realm in cls.realms


class ServerProvider(Provider):
    realms = ("server",)


class ClientProvider(Provider):
    realms = ("client",)


PROVIDERS = (ServerProvider, ClientProvider)


def provider_types():
    return PROVIDERS


for provider_type in provider_types():
    print(provider_type.supports("server"))


class Node:
    def __init__(self, name):
        self.name = name
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


root = Node("somnia.Root")
print([node.name.startswith("somnia.") for node in root.walk()][0])
