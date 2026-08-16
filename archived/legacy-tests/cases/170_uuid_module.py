# expect:
# 12345678123456781234567812345678
# 12345678-1234-5678-1234-567812345678
# UUID('12345678-1234-5678-1234-567812345678')
# True
# False
# 36
# - - - -
# 4
# True

from uuid import UUID, uuid4

u1 = UUID("12345678-1234-5678-1234-567812345678")
print(u1.hex)
print(str(u1))
print(repr(u1))

u2 = UUID("12345678123456781234567812345678")
print(u1 == u2)

u3 = UUID("00000000-0000-0000-0000-000000000001")
print(u1 == u3)

r = uuid4()
s = str(r)
print(len(s))
print(s[8], s[13], s[18], s[23])
print(s[14])
print(s[19] in "89ab")
