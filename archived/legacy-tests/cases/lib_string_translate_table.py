# expect:
# 312
import string
t = str.maketrans('abc', '123')
print('cab'.translate(t))
# asmpython (beta/3.14.0) rejects at compile: [E113] type has no method 'maketrans'
