# tier: spec
# ref: library/stdtypes.html#string-methods
# expect:
# 'Ab Cd'
#  AB CD 
#  ab cd 
# ['Ab', 'Cd']
# a-b
# True
s = ' Ab Cd '
print(repr(s.strip()))
print(s.upper())
print(s.lower())
print(s.strip().split())
print('-'.join(['a', 'b']))
print('abc'.startswith('ab'))
