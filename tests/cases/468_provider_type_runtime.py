# expect:
# 1
# 0
# 1
# 1
# 1
# 1
# Finite class tuples must lower without a dynamic metatype runtime.
# Dynamic values must preserve string behavior.
# Tuple-membership isolation generation 1.

class Provider:
    runtime_realms = ("server", "client")

    @classmethod
    def supports_realm(cls, realm: str) -> bool:
        return realm in cls.runtime_realms


class ServerProvider(Provider):
    runtime_realms = ("server",)


class ClientProvider(Provider):
    runtime_realms = ("client",)


class StaticProbe:
    @staticmethod
    def contains_server(realm: str) -> bool:
        return realm in ("server",)


def starts_with_somnia(value) -> bool:
    return value.startswith("somnia.")


provider_types = (ServerProvider, ClientProvider)
print(provider_types[0].supports_realm("server"))
print(provider_types[1].supports_realm("server"))
print(starts_with_somnia("somnia.Scene"))
print("Provider" in str(Provider))
print("server" in ("server",))
print(StaticProbe.contains_server("server"))
