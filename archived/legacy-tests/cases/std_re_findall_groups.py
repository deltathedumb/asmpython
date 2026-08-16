# probes: findall returns tuples when there are groups
# expect:
# [('a', '1'), ('b', '2')]
import re

print(re.findall(r"(\w)(\d)", "a1 b2"))
