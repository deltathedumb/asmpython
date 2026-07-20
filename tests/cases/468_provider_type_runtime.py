# expect:
# 1
# 0
# SomniaProvider
# 1
# Covers class-valued providers and unannotated dynamic parameters.
# Semantic/backend probe generation 2.


class Provider:
    runtime_realms = ("server", "client")

    @classmethod
    def supports_realm(cls, realm: str) -> bool:
        return realm in cls.runtime_realms


class ServerProvider(Provider):
    runtime_realms = ("server",)


class ClientProvider(Provider):
    runtime_realms = ("client",)


def starts_with_somnia(value) -> bool:
    return value.startswith("somnia.")


provider_types = (ServerProvider, ClientProvider)
print(provider_types[0].supports_realm("server"))
print(provider_types[1].supports_realm("server"))
print(str(Provider))
print(starts_with_somnia("somnia.Scene"))
