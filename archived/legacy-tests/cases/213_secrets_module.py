# expect:
# True
# True
# 16

import secrets

n: int = secrets.randbits(8)
print(n >= 0)

m: int = secrets.randbelow(100)
print(m >= 0)

tok: str = secrets.token_hex(8)
print(len(tok))
