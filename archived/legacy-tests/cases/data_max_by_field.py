# expect:
# b
products = [{'name': 'a', 'price': 10}, {'name': 'b', 'price': 30}, {'name': 'c', 'price': 20}]
costliest = max(products, key=lambda p: p['price'])
print(costliest['name'])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (min/max key lambda body)
