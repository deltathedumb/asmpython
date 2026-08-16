# expect:
# 1,234,567
def group_digits(n):
    s = str(n)
    result = ''
    for i, digit in enumerate(reversed(s)):
        if i > 0 and i % 3 == 0:
            result = ',' + result
        result = digit + result
    return result
print(group_digits(1234567))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
