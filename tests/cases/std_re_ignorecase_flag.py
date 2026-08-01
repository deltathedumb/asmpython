# probes: re.IGNORECASE is honoured
# expect:
# True
# False
import re

print(re.match("abc", "ABC", re.IGNORECASE) is not None)
print(re.match("abc", "ABC") is not None)
