# expect:
# dlrow olleh
# hlowrd
# el ol
# hlo
# hello
# hello
# ll

s = "hello world"

# Reverse.
print(s[::-1])

# Every other char (forward).
print(s[::2])

# Every other char starting at 1.
print(s[1::2])

# Slice with all three.
print(s[0:5:2])

# Step = 1 acts like normal slice.
print(s[0:5:1])
print(s[:5:1])

# Negative step with explicit endpoints.
print(s[3:1:-1])
