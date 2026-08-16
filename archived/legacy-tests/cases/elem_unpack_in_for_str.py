# probes: a for target destructures each element (str elements)
# expect:
# aa bb
# cc dd
pairs = [("aa", "bb"), ("cc", "dd")]
for left, right in pairs:
    print(left, right)
