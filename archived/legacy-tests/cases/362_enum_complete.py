# expect:
# 1
# 2
# 3
# 6
# 0

import enum

e1: enum.Enum = enum.Enum(1)
e2: enum.Enum = enum.Enum(2)
e3: enum.Enum = enum.Enum(3)
print(e1.value)
print(e2.value)
print(e3.value)

vals: list[int] = enum.show_flag_values(6)
total: int = 0
for v in vals:
    total = total + v
print(total)

print(enum.CONFORM)
