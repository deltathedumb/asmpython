# expect:
# a2b3c1
def compress(s):
    if not s:
        return ''
    result = ''
    count = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            count += 1
        else:
            result += s[i - 1] + str(count)
            count = 1
    result += s[-1] + str(count)
    return result
print(compress('aabbbc'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
