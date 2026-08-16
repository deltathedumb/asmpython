# expect:
# True
# False
# True
# True
# False
# True

# set()/frozenset() constructors. set(list) builds a set from elements;
# frozenset(setliteral) passes a set through. Membership is the only op.

words = ["if", "else", "while"]
s = set(words)
print("if" in s)        # True
print("for" in s)       # False
print("while" in s)     # True

kw = frozenset({"def", "return"})
print("def" in kw)      # True
print("class" in kw)    # False

empty = set()
print("x" not in empty) # True
