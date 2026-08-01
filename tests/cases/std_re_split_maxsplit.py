# probes: re.split accepts maxsplit
# expect:
# ['a', 'b c']
import re

print(re.split(r"\s+", "a b c", maxsplit=1))
