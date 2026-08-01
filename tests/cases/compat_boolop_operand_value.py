# guards: boolop_value_compat_fixes
# expect:
# default
# set
# 5
# 4
# empty-wins
# anon
def pick(primary, fallback):
    return primary or fallback


print(pick("", "default"))
print(pick("set", "default"))
print(0 or 5)
print(3 and 4)
print("" or "empty-wins")
name = "" or "anon"
print(name)
