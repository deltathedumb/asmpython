# expect:
# 1
# 0
# 1
# 1
# 0
# 1

# set()/frozenset() constructors. set(list) builds a set from elements;
# frozenset(setliteral) passes a set through. Membership is the only op.

words = ["if", "else", "while"]
s = set(words)
print("if" in s)        # 1
print("for" in s)       # 0
print("while" in s)     # 1

kw = frozenset({"def", "return"})
print("def" in kw)      # 1
print("class" in kw)    # 0

empty = set()
print("x" not in empty) # 1
