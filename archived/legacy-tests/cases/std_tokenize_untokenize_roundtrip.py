# probes: untokenize rebuilds equivalent source
# expect:
# a = 1
import io
import tokenize

source = "a = 1\n"
tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
print(tokenize.untokenize(tokens).strip())
