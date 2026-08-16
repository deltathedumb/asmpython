# expect:
# 1
# 1
# dns

from uuid import uuid4, uuid1, uuid3, uuid5, NAMESPACE_DNS, UUID

u = uuid4()
print(1 if len(str(u)) == 36 else 0)

u1 = uuid1()
print(1 if len(str(u1)) == 36 else 0)

print("dns")
