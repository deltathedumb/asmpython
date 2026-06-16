# probe87: complex string operations - multiline, escape sequences, raw strings
# Escape sequences
print("tab:\there")          # tab:	here
print("newline:\nhere")      # newline:\nhere (two lines)
print("quote: \"hello\"")    # quote: "hello"
print("backslash: \\")       # backslash: \
print("null: \0end")         # null: (null char)end

# Raw strings (no escapes)
r = r"hello\nworld"
print(r)   # hello\nworld (literal backslash n)
print(len(r))   # 12

# String repetition
s = "ab" * 3
print(s)   # ababab

# String comparison
print("abc" < "abd")   # True
print("abc" > "ab")    # True
print("abc" == "abc")  # True
print("abc" != "ABC")  # True

# String in list
words = ["hello", "world", "foo"]
print("hello" in words)   # True
print("bar" in words)     # False

# String methods chain
print("  hello world  ".strip().upper())   # HELLO WORLD
