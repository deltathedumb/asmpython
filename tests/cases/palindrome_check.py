# expect:
# True False
def is_pal(s):
    return s == s[::-1]
print(is_pal('racecar'), is_pal('hello'))
# asmpython (beta/3.14.0) MISMATCH: prints 'False False\n' (wrong).
