# expect:
# ['1', '22', '333']
import re
print(re.findall(r'\d+', 'a1b22c333'))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
