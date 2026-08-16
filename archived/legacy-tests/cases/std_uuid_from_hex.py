# probes: uuid.UUID parses a hex string and exposes .int
# expect:
# 12345678-1234-5678-1234-567812345678
# 24197857161011715162171839636988778104
import uuid

u = uuid.UUID("12345678-1234-5678-1234-567812345678")
print(str(u))
print(u.int)
