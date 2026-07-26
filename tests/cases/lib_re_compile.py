# expect:
# ['1', '22']
import re
pat = re.compile(r'\d+')
print(pat.findall('a1b22c'))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'findall'
