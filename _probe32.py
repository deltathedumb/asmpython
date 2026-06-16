# probe32: string formatting edge cases, string parsing
# Multiline string
s = """hello
world"""
print(len(s))   # 11 (hello\nworld = 5+1+5=11)
print(s[0])     # h

# Raw string
r = r"hello\nworld"
print(len(r))   # 12 (no escape)

# String with escape
e = "tab\there"
print(e[3])     # t (after tab char)

# str.encode equivalent - just bytes check
print(ord("\n"))    # 10
print(ord("\t"))    # 9
print(ord("\\"))    # 92

# int from hex/octal literal
print(0xFF)   # 255
print(0o17)   # 15
print(0b1010) # 10
