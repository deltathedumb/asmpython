# probes: calendar.weekday is Monday-zero
# expect:
# 0
import calendar

print(calendar.weekday(2020, 1, 6))
