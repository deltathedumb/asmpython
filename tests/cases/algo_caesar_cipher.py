# expect:
# khoor
def caesar(s, shift):
    result = ''
    for c in s:
        if c.isalpha():
            base = ord('a')
            result += chr((ord(c) - base + shift) % 26 + base)
        else:
            result += c
    return result
print(caesar('hello', 3))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (ord non-str)
