# expect:
# -1
print(min([], default=-1))
# asmpython (beta/3.14.0) rejects at compile: [E021] unexpected keyword argument 'default'
