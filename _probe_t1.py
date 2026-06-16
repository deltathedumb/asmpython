# Test: enumerate()
fruits = ["apple", "banana", "cherry"]
for i, fruit in enumerate(fruits):
    print(i, fruit)

# enumerate with start
for i, v in enumerate([10, 20, 30], start=1):
    print(i, v)
