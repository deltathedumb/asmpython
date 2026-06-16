# probe101: complex string building patterns
# String as list of chars
s = "hello"
chars = list(s)
print(len(chars))    # 5
print(chars[0])      # h
print(chars[4])      # o

# join back
rebuilt = "".join(chars)
print(rebuilt)   # hello

# String reversal
rev = "".join(reversed(s))
print(rev)   # olleh

# Character manipulation
def caesar(text: str, shift: int) -> str:
    result = []
    for c in text:
        if "a" <= c <= "z":
            result.append(chr((ord(c) - ord("a") + shift) % 26 + ord("a")))
        elif "A" <= c <= "Z":
            result.append(chr((ord(c) - ord("A") + shift) % 26 + ord("A")))
        else:
            result.append(c)
    return "".join(result)

print(caesar("Hello World!", 3))   # Khoor Zruog!
print(caesar("Khoor Zruog!", -3))  # Hello World!

# Count characters
def char_freq(s: str) -> dict:
    freq: dict = {}
    for c in s:
        if c in freq:
            freq[c] = freq[c] + 1
        else:
            freq[c] = 1
    return freq

freq = char_freq("hello world")
print(freq["l"])   # 3
print(freq["o"])   # 2
print(freq[" "])   # 1
