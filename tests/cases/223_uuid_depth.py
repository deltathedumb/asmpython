# expect:
# 36
# 32
# 4

import uuid

u = uuid.uuid4()
s: str = str(u)
print(len(s))
print(len(u.hex))
print(u.hex[12])
