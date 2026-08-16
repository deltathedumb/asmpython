# expect:
# cat elephant
words = ['cat', 'elephant', 'dog']
print(min(words, key=len), max(words, key=len))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal, a name bound to a lambda, or a top-level function ('len' is none of these)
