# expect:
# caught

try:
    raise ValueError("boom")
except ValueError:
    print("caught")
