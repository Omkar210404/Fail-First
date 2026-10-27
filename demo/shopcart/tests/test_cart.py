from shopcart import Cart


def test_subtotal():
    cart = Cart()
    cart.add("pen", 1.50, qty=2)
    cart.add("pad", 3.00)
    assert cart.subtotal() == 6.0


def test_bulk_discount_for_large_orders():
    cart = Cart()
    cart.add("pen", 1.00, qty=12)
    assert cart.total() == 10.8
