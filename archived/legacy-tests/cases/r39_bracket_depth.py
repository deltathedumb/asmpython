# expect:
# 3
def max_depth(s):
    depth = 0
    max_d = 0
    for c in s:
        if c == '(':
            depth += 1
            max_d = max(max_d, depth)
        elif c == ')':
            depth -= 1
    return max_d
print(max_depth('((()))(())'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
