# expect:
# 5
# ok

# Exercises `# [compiler: ignore_start] ... ignore_end]`: the block holds
# linter-facing placeholder code the compiler must skip entirely. If the
# compiler saw it, `undefined_helper` / `mystery` would be undefined-name
# errors; instead the block is blanked out before lexing.

# [compiler: ignore_start]
class LinterOnlyStub:
    def helper(self):
        return undefined_helper(mystery, another_missing)
# [compiler: ignore_end]


def run():
    print(2 + 3)
    print("ok")


run()
