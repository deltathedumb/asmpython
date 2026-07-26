# expect:
# aaabbc
def decode(encoded):
    result = ''
    i = 0
    while i < len(encoded):
        char = encoded[i]
        count = int(encoded[i + 1])
        result += char * count
        i += 2
    return result
print(decode('a3b2c1'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
