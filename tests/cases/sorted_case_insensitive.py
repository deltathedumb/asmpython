# expect:
# ['apple', 'Banana', 'Cherry']
words = ['Banana', 'apple', 'Cherry']
print(sorted(words, key=str.lower))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal or a name bound to a lambda
