"""Cart totals with a bulk discount."""

BULK_THRESHOLD = 10  # orders of this many items or more get the bulk discount
BULK_RATE = 0.10


class Cart:
    def __init__(self):
        self.items: dict[str, tuple[float, int]] = {}

    def add(self, name: str, price: float, qty: int = 1) -> None:
        if price < 0 or qty < 1:
            raise ValueError("price must be >= 0 and qty >= 1")
        old_price, old_qty = self.items.get(name, (price, 0))
        self.items[name] = (price, old_qty + qty)

    def item_count(self) -> int:
        return sum(qty for _, qty in self.items.values())

    def subtotal(self) -> float:
        return sum(price * qty for price, qty in self.items.values())

    def total(self) -> float:
        amount = self.subtotal()
        if self.item_count() > BULK_THRESHOLD:
            amount *= 1 - BULK_RATE
        return round(amount, 2)
