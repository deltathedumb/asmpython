# tier: spec
# ref: peps.python.org/pep-0498/
# expect:
# 7
# 8
# 'ab'
# 3.14
# 0007
#    ab|
# {literal}
n = 7
s = 'ab'
f = 3.14159
print(f'{n}')
print(f'{n + 1}')
print(f'{s!r}')
print(f'{f:.2f}')
print(f'{n:04d}')
print(f'{s:>5}|')
print(f'{{literal}}')
