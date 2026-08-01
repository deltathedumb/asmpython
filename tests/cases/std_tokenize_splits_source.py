# probes: tokenize yields the tokens of a source string
# expect:
# ['NAME:x', 'OP:=', 'NUMBER:1']
import io
import token
import tokenize

source = io.StringIO("x = 1\n")
kinds = []
for tok in tokenize.generate_tokens(source.readline):
    if tok.type in (token.NAME, token.OP, token.NUMBER):
        kinds.append(token.tok_name[tok.type] + ":" + tok.string)
print(kinds)
