import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import (parse_receipt, compute_split, settle, explain,
                 reminder, capture_order, create_order, new_tokens)

PEOPLE = ["Alice", "Bob", "Carol"]
RECEIPT = """Margherita Pizza 24.00
Caesar Salad (Alice) 14.00
Truffle Pasta (Bob) 22.00
Sparkling Water (Carol) 6.00
Tax 5.28
Tip 11.88
Total 83.16"""

def split():
    items, tax, tip, declared, _ = parse_receipt(RECEIPT, PEOPLE)
    shares, total = compute_split(items, tax, tip)
    return shares, total, declared

def test_parse():
    items, tax, tip, declared, _ = parse_receipt(RECEIPT, PEOPLE)
    assert len(items) == 4 and tax == 5.28 and tip == 11.88
    assert declared == 83.16
    assert items[0]["people"] == PEOPLE  # unnamed = shared by all

def test_price_first_and_quantity_lines():
    items, *_ = parse_receipt("14.00 Caesar Salad (Alice)\nCoffee x2 8.00", PEOPLE)
    assert items[0]["amount"] == 14.00 and items[0]["people"] == ["Alice"]
    assert items[1]["amount"] == 8.00 and "x2" in items[1]["name"]

def test_shares_sum_to_total():
    shares, total, _ = split()
    assert total == 83.16
    assert round(sum(s["total"] for s in shares.values()), 2) == 83.16
    assert shares["Alice"]["subtotal"] == 22.00
    assert shares["Bob"]["subtotal"] == 30.00
    assert shares["Carol"]["subtotal"] == 14.00

def test_settle_with_payer():
    shares, total, _ = split()
    owed = settle(shares, "Alice")
    assert owed["Alice"] == 0.0            # payer's own share is settled
    assert owed["Bob"] == shares["Bob"]["total"]
    assert round(sum(owed.values()), 2) == round(total - shares["Alice"]["total"], 2)

def test_settle_without_payer():
    shares, _, _ = split()
    owed = settle(shares, "")
    assert owed == {k: v["total"] for k, v in shares.items()}

def test_explain_mentions_collection_total():
    shares, total, _ = split()
    owed = settle(shares, "Alice")
    text = "\n".join(explain(shares, owed, "Alice", total))
    assert "Owes" in text and "collect back" in text

def test_duplicate_warning():
    *_, w = parse_receipt("Coffee 4.00\nCoffee 4.00", PEOPLE)
    assert any("duplicate" in x for x in w)

def test_reminder_mentions_amount_and_items():
    shares, _, _ = split()
    t = reminder("Bob", shares["Bob"], "Alice", "polite")
    assert "Bob" in t and "$" in t and "Truffle Pasta" in t

def test_simulated_capture_completes():
    r = capture_order("SIM-TEST123")
    assert r["status"] == "COMPLETED" and r["mode"] == "sandbox-demo"

def test_euro_lines_parse():
    items, *_ = parse_receipt("Salad (Alice) €14.00\n€22.00 Pasta (Bob)", PEOPLE)
    assert items[0]["amount"] == 14.00 and items[1]["amount"] == 22.00

def test_order_payload_uses_currency():
    r = create_order("Bob", 10.0, "test", currency="EUR")
    amt = r["payload"]["purchase_units"][0]["amount"]
    assert amt["currency_code"] == "EUR" and amt["value"] == "10.00"

def test_tokens_only_for_debtors_and_unique():
    owed = {"Alice": 0.0, "Bob": 37.80, "Carol": 17.64}
    tokens = new_tokens(owed)
    assert sorted(tokens.values()) == ["Bob", "Carol"]
    assert len(set(tokens.keys())) == 2 and all(len(t) >= 10 for t in tokens)

if __name__ == "__main__":
    for fn in [test_parse, test_price_first_and_quantity_lines,
               test_shares_sum_to_total, test_settle_with_payer,
               test_settle_without_payer, test_explain_mentions_collection_total,
               test_duplicate_warning, test_reminder_mentions_amount_and_items,
               test_simulated_capture_completes, test_euro_lines_parse,
               test_order_payload_uses_currency,
               test_tokens_only_for_debtors_and_unique]:
        fn()
    print("all tests passed")
