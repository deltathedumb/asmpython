# probes: textwrap.shorten truncates with a placeholder
# expect:
# one [...]
import textwrap

print(textwrap.shorten("one two three four", width=12))
