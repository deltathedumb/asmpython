# probes: the token module exposes its type constants
# expect:
# NAME
# NUMBER
# False
import token

print(token.tok_name[token.NAME])
print(token.tok_name[token.NUMBER])
print(token.NAME == token.NUMBER)
