# expect:
# 3
def count_char(s, target):
    n = 0
    for c in s:
        if c == target:
            n += 1
    return n
print(count_char('banana', 'a'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
