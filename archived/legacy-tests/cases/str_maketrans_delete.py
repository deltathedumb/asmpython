# expect:
# heo
print('hello'.translate(str.maketrans('', '', 'l')))
# asmpython (beta/3.14.0) rejects at compile: [E113] type has no method 'maketrans'
