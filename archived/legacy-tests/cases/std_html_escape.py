# probes: html.escape replaces markup characters
# expect:
# &lt;a href=&#x27;x&#x27;&gt;&amp;&lt;/a&gt;
import html

print(html.escape("<a href='x'>&</a>"))
