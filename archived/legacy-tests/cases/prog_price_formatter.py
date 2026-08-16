# expect:
# ['$9.99', '$19.50', '$100.00']
prices = [9.99, 19.5, 100]
formatted = ['$' + format(p, '.2f') for p in prices]
print(formatted)
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (float and int); mixed-type lists need a tagged-value runtime, not yet implemented
