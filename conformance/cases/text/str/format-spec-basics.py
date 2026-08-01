# tier: spec
# ref: library/string.html#format-specification-mini-language
# expect:
#     ab
# ab    |
#   ab  |
# 0007
# 3.142
# +5
print('{:>6}'.format('ab'))
print('{:<6}|'.format('ab'))
print('{:^6}|'.format('ab'))
print('{:04d}'.format(7))
print('{:.3f}'.format(3.14159))
print('{:+d}'.format(5))
