# expect:
# user host
import re
m = re.search(r'(\w+)@(\w+)', 'user@host')
print(m.group(1), m.group(2))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'group'
