# expect:
# a[1]b[2]
import re
print(re.sub(r'\d', lambda m: '[' + m.group() + ']', 'a1b2'))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'group'
