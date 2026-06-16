# walrus operator
data = [1, 5, 2, 8, 3, 9]
if (n := len(data)) > 3:
    print(n)

# walrus in comprehension
evens = [y for x in data if (y := x * 2) > 6]
print(evens)
