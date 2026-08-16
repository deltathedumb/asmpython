# expect:
# 2
def char_freq(s):
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    return freq
result = char_freq('hello')
print(result['l'])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
