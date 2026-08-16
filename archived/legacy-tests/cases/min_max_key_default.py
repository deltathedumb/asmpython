# expect:
# empty
items = []
print(max(items, key=len, default='empty'))
# asmpython (beta/3.14.0) rejects at compile: [E021] unexpected keyword argument 'default'
