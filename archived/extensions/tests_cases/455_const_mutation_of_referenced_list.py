# ext: constants
# expect:
# 1
# 2
# 3
# 4

const XS = [1, 2, 3]
XS.append(4)
for x in XS:
    print(x)
