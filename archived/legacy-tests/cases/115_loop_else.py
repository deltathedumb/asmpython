# expect:
# 0
# 1
# 2
# for done
# 0
# w 0
# w 1
# w 2
# while done
# w 0
# w 1
# after

for i in range(3):
    print(i)
else:
    print("for done")

for i in range(3):
    if i == 1:
        break
    print(i)
else:
    print("not reached")

i = 0
while i < 3:
    print("w", i)
    i += 1
else:
    print("while done")

i = 0
while i < 5:
    if i == 2:
        break
    print("w", i)
    i += 1
else:
    print("while not reached")
print("after")
