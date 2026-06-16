# probe67: string join, ord, chr, str.format
words = ["hello", "world", "foo"]
print(", ".join(words))        # hello, world, foo
print(" ".join(words))         # hello world foo
print("".join(words))          # helloworldfoo
print("-".join(["a", "b"]))    # a-b

# ord and chr
print(ord("A"))    # 65
print(ord("a"))    # 97
print(ord("0"))    # 48
print(chr(65))     # A
print(chr(97))     # a
print(chr(48))     # 0

# string contains / in
s = "hello world"
print("hello" in s)     # True
print("xyz" in s)       # False
print("world" in s)     # True

# str.upper / lower (already tested but confirm)
print("hello".upper())   # HELLO
print("WORLD".lower())   # world
