# expect:
# ['a', 'b', 'c', 'd']
import re
print(re.split(r'[,;]', 'a,b;c,d'))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
