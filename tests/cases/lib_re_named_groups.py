# expect:
# 2020 03
import re
m = re.match(r'(?P<y>\d{4})-(?P<m>\d{2})', '2020-03')
print(m.group('y'), m.group('m'))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'group'
