# tier: spec
# ref: peps.python.org/pep-0498/
# expect:
# 3.14
#   3.14|
# x=42
w = 6
v = 3.14159
print(f'{v:.{2}f}')
print(f'{v:{w}.2f}|')
x = 42
print(f'{x=}')
