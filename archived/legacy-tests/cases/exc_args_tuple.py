# expect:
# ('a', 'b', 'c')
try:
    raise ValueError('a', 'b', 'c')
except ValueError as e:
    print(e.args)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
