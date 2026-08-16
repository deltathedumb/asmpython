# expect:
# raised at 3
# survived
# 0
# 1
# 2
# 3
# 4
for i in range(5):
    try:
        if i == 3:
            raise "boom"
    except as e:
        print("raised at", i)
print("survived")
for j in range(5):
    print(j)
