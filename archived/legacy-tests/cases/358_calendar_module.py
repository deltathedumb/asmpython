# expect:
# 1
# 0
# 0
# 29
# 0

import calendar

print(calendar.isleap(2024))
print(calendar.isleap(1900))
print(calendar.isleap(2023))
print(calendar._days_in_month(2024, 2))
info: list[int] = calendar.monthrange(2024, 1)
print(info[0])
