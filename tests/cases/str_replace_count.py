# expect:
# bba
print('aaa'.replace('a', 'b', 2))
# str.replace()'s 3rd count arg is rejected ([E021]); CPython returns 'bba'.
