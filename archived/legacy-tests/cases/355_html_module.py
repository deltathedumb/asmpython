# expect:
# &lt;b&gt;Hello &amp; &quot;world&quot;&#x27;
# <b>Hello & "world"'

import html

s: str = html.escape("<b>Hello & \"world\"'")
print(s)
s2: str = html.unescape("&lt;b&gt;Hello &amp; &quot;world&quot;&#x27;")
print(s2)
