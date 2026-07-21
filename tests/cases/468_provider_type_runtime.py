# expect:
# 1
# 0
# 1
# Finite class tuples must lower without a dynamic metatype runtime.
# Verification generation 6.

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
print(starts_with_somnia("somnia.Scene"))
