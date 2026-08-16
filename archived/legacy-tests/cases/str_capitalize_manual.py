# expect:
# Hello
def cap(s):
    if not s:
        return s
    return s[0].upper() + s[1:].lower()
print(cap('hELLO'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
