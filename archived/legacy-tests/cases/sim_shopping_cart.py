# expect:
# total: 3.5
cart = []
cart.append(('apple', 0.5, 3))
cart.append(('bread', 2.0, 1))
total = 0.0
for name, price, qty in cart:
    total += price * qty
print('total:', round(total, 2))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
