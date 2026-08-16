# expect:
# 0
# 1
# 2
# 3
# excel
# 1

from csv import QUOTE_MINIMAL, QUOTE_ALL, QUOTE_NONNUMERIC, QUOTE_NONE
from csv import Dialect, register_dialect, get_dialect, list_dialects

print(QUOTE_MINIMAL)
print(QUOTE_ALL)
print(QUOTE_NONNUMERIC)
print(QUOTE_NONE)

d = Dialect("excel")
print(d.name)

register_dialect("mycsv", d)
dialects = list_dialects()
found: int = 0
for name in dialects:
    if name == "mycsv":
        found = 1
print(found)
