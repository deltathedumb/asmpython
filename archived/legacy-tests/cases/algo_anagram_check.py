# expect:
# True False
def is_anagram(a, b):
    return sorted(a) == sorted(b)
print(is_anagram('listen', 'silent'), is_anagram('a', 'b'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
