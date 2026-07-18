# ext: access
# expect-error: is private

class Account:
    def __init__(self, balance: int) -> None:
        self.balance = balance

    @private
    def _raw_balance(self) -> int:
        return self.balance

a = Account(42)
print(a._raw_balance())
