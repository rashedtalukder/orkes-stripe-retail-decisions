from stripe_decisions.facts import find_duplicate, normalize

NOW = 1_800_000_000
CUST = {"id": "cus_1", "livemode": False}


def ch(id, amount, created, status="succeeded", ref="R1", refunded=False, amount_refunded=0, currency="usd"):
    return {"id": id, "amount": amount, "currency": currency, "created": created, "status": status, "paid": status == "succeeded",
            "refunded": refunded, "amount_refunded": amount_refunded, "metadata": {"osd_billing_ref": ref},
            "description": "x", "livemode": False}


def facts(charges=(), invoices=(), subs=(), charge_id=None, customer=CUST):
    return normalize(customer, list(charges), list(invoices), list(subs), charge_id=charge_id, requested_cents=0, now=NOW)


def test_duplicate_requires_same_amount_ref_and_window():
    assert find_duplicate([ch("a", 3500, NOW), ch("b", 3500, NOW + 60)])
    assert not find_duplicate([ch("a", 3500, NOW), ch("b", 3400, NOW + 60)])
    assert not find_duplicate([ch("a", 3500, NOW), ch("b", 3500, NOW + 60, ref="R2")])
    assert not find_duplicate([ch("a", 3500, NOW), ch("b", 3500, NOW + 4 * 86400)])
    assert not find_duplicate([ch("a", 3500, NOW), ch("b", 3500, NOW + 60, status="failed")])


def test_verified_duplicate_reports_refundable_cents_of_second_charge():
    f = facts([ch("a", 3500, NOW), ch("b", 3500, NOW + 60, amount_refunded=500)])
    assert f.duplicateVerified == "YES" and f.duplicateChargeId == "b" and f.duplicateRefundableCents == 3000


def test_one_success_one_decline_is_not_a_duplicate():
    f = facts([ch("a", 4900, NOW), ch("b", 4900, NOW + 60, status="failed")])
    assert f.duplicateVerified == "NO" and f.evidence["failedCharges"] == 1


def test_refunded_target():
    f = facts([ch("a", 4900, NOW, refunded=True, amount_refunded=4900)])
    assert f.alreadyRefunded == "YES"


def test_customer_missing_is_insufficient():
    assert facts(customer=None).factsStatus == "INSUFFICIENT"


def test_no_history_is_insufficient():
    assert facts().factsStatus == "INSUFFICIENT"


def test_foreign_charge_reference_is_conflicting():
    assert facts([ch("a", 4900, NOW)], charge_id="ch_other").factsStatus == "CONFLICTING"


def test_livemode_object_is_error():
    bad = ch("a", 4900, NOW)
    bad["livemode"] = True
    assert facts([bad]).factsStatus == "ERROR"


def test_proration_detected_from_invoice_line_parent():
    inv = {"id": "in_1", "status": "paid", "created": NOW - 86400, "livemode": False,
           "lines": {"data": [{"parent": {"subscription_item_details": {"proration": True}}}]}}
    f = facts([ch("a", 1900, NOW - 20 * 86400), ch("b", 1500, NOW - 86400, ref="R2")], invoices=[inv])
    assert f.prorationPresent == "YES" and f.duplicateVerified == "NO"


def test_failed_renewal_detected():
    inv = {"id": "in_2", "status": "open", "attempted": True, "amount_remaining": 4900, "created": NOW, "livemode": False,
           "lines": {"data": []}}
    f = facts([ch("a", 4900, NOW - 30 * 86400)], invoices=[inv], subs=[{"status": "past_due", "livemode": False}])
    assert f.paymentFailed == "YES"
