# probes: a match reports its span
# expect:
# 2
# 5
# 123
import re

m = re.search(r"\d+", "ab123cd")
print(m.start())
print(m.end())
print(m.group(0))
