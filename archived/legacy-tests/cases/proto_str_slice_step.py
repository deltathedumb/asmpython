# probes: a str slice honours a negative step
# expect:
# fedcba
# bcd
# ace
s = "abcdef"
print(s[::-1])
print(s[1:4])
print(s[::2])
