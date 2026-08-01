# tier: impl
# expect:
# True
# False
a = 'hello'
b = 'hello'
print(a is b)
c = ''.join(['hel', 'lo'])
print(c is a)
