# probes: re.sub accepts a count limit
# expect:
# XXa
import re

print(re.sub("a", "X", "aaa", count=2))
