# expect:
# before
# caught
# after
print("before")
try:
    raise "oops"
    print("unreached")
except:
    print("caught")
print("after")
