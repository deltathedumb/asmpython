# expect:
# Zm9v
# foo
# foobar
# ++++
# ----
# True
# MZXW6===
# foo
# 666F6F
# foo

from base64 import b64encode, b64decode, urlsafe_b64encode, urlsafe_b64decode, b32encode, b32decode, b16encode, b16decode


def to_str(data: list[int]) -> str:
    return "".join([chr(b) for b in data])


def to_bytes(s: str) -> list[int]:
    out: list[int] = []
    for ch in s:
        out.append(ord(ch))
    return out


# b64 round-trip (with padding) and concat
print(to_str(b64encode(to_bytes("foo"))))
print(to_str(b64decode(b64encode(to_bytes("foo")))))
print(to_str(b64decode(to_bytes("Zm9vYmFy"))))

# urlsafe vs standard alphabet on bytes that produce + and /
data = [251, 239, 190]
print(to_str(b64encode(data)))
print(to_str(urlsafe_b64encode(data)))
print(to_str(urlsafe_b64decode(urlsafe_b64encode(data))) == to_str(data))

# b32 round-trip (with padding)
print(to_str(b32encode(to_bytes("foo"))))
print(to_str(b32decode(b32encode(to_bytes("foo")))))

# b16 (hex) round-trip
print(to_str(b16encode(to_bytes("foo"))))
print(to_str(b16decode(b16encode(to_bytes("foo")))))
