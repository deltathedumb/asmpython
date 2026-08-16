# expect:
# 3
def count_vowels(s):
    return sum(1 for c in s.lower() if c in 'aeiou')
print(count_vowels('Hello World'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
