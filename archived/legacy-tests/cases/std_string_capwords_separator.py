# probes: string.capwords accepts a separator
# expect:
# A-B c
import string

print(string.capwords("a-b c", "-"))
