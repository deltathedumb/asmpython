# probes: secrets.token_hex returns the requested width
# expect:
# 16
import secrets

print(len(secrets.token_hex(8)))
