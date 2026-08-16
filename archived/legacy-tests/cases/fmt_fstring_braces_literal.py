# probes: doubled braces emit a literal brace
# expect:
# {1}
# {literal}
v = 1
print(f"{{{v}}}")
print(f"{{literal}}")
