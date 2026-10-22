"""Policy engine tests: the rule table, precedence, ordering and wording."""
from __future__ import annotations

import re
import unittest

from veto.policy import RULE_KINDS, evaluate


def agent(**overrides) -> dict:
    base = {
        "id": "agt_00000000",
        "name": "Pantry runner",
        "tag": "pantry",
        "channel": "#pantry",
        "status": "active",
        "monthly_cap_cents": 80_000,
        "per_order_cap_cents": 30_000,
        "per_unit_cap_cents": 1_500,
        "approval_threshold_cents": 5_000,
        "vendors": ["amazon.com", "grove.co"],
        "categories": ["pantry"],
        "rules": list(RULE_KINDS),
        "overrides": [],
    }
    base.update(overrides)
    return base


def line(**overrides) -> dict:
    base = {
        "sku": "sku-1",
        "title": "Bamboo toilet paper, 24 rolls",
        "qty": 1,
        "unit_cents": 1_000,
        "line_cents": 1_000,
        "vendor": "amazon.com",
        "category": "pantry",
        "brand": "",
        "image": "",
        "url": "",
        "source": "local",
    }
    base.update(overrides)
    return base


def rules(reasons: list[dict]) -> list[str]:
    return [reason["rule"] for reason in reasons]


class PolicyTests(unittest.TestCase):
    # -- the calm case --------------------------------------------------
    def test_auto_when_inside_every_rule(self) -> None:
        decision = evaluate(agent=agent(), lines=[line()])
        self.assertEqual(decision["outcome"], "auto")
        self.assertEqual(decision["reasons"], [])

    def test_cart_total_is_summed_from_lines(self) -> None:
        decision = evaluate(
            agent=agent(per_unit_cap_cents=None),
            lines=[line(qty=2, unit_cents=1_200, line_cents=2_400),
                   line(sku="sku-2", unit_cents=2_600)],
        )
        # 2400 + 2600 = 5000, exactly at the approval threshold: not over.
        self.assertEqual(decision["outcome"], "auto")

    # -- review rules ---------------------------------------------------
    def test_approval_threshold_review(self) -> None:
        over = evaluate(
            agent=agent(per_unit_cap_cents=None),
            lines=[line(unit_cents=6_000, line_cents=6_000)],
        )
        self.assertEqual(over["outcome"], "review")
        self.assertEqual(rules(over["reasons"]), ["approval_threshold"])
        reason = over["reasons"][0]
        self.assertEqual(reason["severity"], "review")
        self.assertEqual(reason["observed_cents"], 6_000)
        self.assertEqual(reason["limit_cents"], 5_000)
        self.assertIn("$60.00 over the $50.00 approval threshold", reason["message"])
        self.assertEqual(reason["unblock"], "cart must stay under $50.00")

    def test_per_unit_review_includes_observed_and_limit(self) -> None:
        over = evaluate(
            agent=agent(approval_threshold_cents=100_000),
            lines=[line(unit_cents=1_800, line_cents=1_800)],
        )
        self.assertEqual(over["outcome"], "review")
        reason = over["reasons"][0]
        self.assertEqual(reason["rule"], "per_unit_cap")
        self.assertEqual(reason["observed_cents"], 1_800)
        self.assertEqual(reason["limit_cents"], 1_500)
        self.assertIn("$18.00 per unit against a $15.00 cap", reason["message"])

    def test_one_per_unit_reason_per_offending_line(self) -> None:
        over = evaluate(
            agent=agent(per_order_cap_cents=100_000, approval_threshold_cents=100_000),
            lines=[
                line(title="Split keyboard", unit_cents=12_800, line_cents=12_800,
                     category="pantry"),
                line(title="Standing mat", unit_cents=8_900, line_cents=8_900,
                     category="pantry"),
            ],
        )
        self.assertEqual(over["outcome"], "review")
        self.assertEqual(rules(over["reasons"]).count("per_unit_cap"), 2)
        messages = " ".join(reason["message"] for reason in over["reasons"])
        self.assertIn("Split keyboard", messages)
        self.assertIn("Standing mat", messages)

    # -- block rules ----------------------------------------------------
    def test_vendor_block(self) -> None:
        blocked = evaluate(agent=agent(), lines=[line(vendor="staples.com")])
        self.assertEqual(blocked["outcome"], "block")
        self.assertEqual(rules(blocked["reasons"]), ["vendor_allowlist"])
        reason = blocked["reasons"][0]
        self.assertEqual(reason["severity"], "block")
        self.assertIn("staples.com is not an approved merchant", reason["message"])
        self.assertEqual(reason["unblock"], "add staples.com to approved merchants")

    def test_vendor_suffix_match(self) -> None:
        self.assertEqual(
            evaluate(agent=agent(), lines=[line(vendor="www.amazon.com")])["outcome"], "auto"
        )
        self.assertEqual(
            evaluate(agent=agent(), lines=[line(vendor="notamazon.com")])["outcome"], "block"
        )

    def test_category_block(self) -> None:
        blocked = evaluate(agent=agent(), lines=[line(category="equipment")])
        self.assertEqual(blocked["outcome"], "block")
        reason = blocked["reasons"][0]
        self.assertEqual(reason["rule"], "category_allowlist")
        self.assertIn("equipment is not an approved category", reason["message"])

    def test_empty_vendor_list_blocks_every_line(self) -> None:
        blocked = evaluate(agent=agent(vendors=[]), lines=[line()])
        self.assertEqual(blocked["outcome"], "block")
        self.assertEqual(rules(blocked["reasons"]), ["vendor_allowlist"])

    def test_per_order_block(self) -> None:
        blocked = evaluate(
            agent=agent(per_unit_cap_cents=None, approval_threshold_cents=None),
            lines=[line(unit_cents=31_000, line_cents=31_000)],
        )
        self.assertEqual(blocked["outcome"], "block")
        reason = blocked["reasons"][0]
        self.assertEqual(reason["rule"], "per_order_cap")
        self.assertEqual(reason["observed_cents"], 31_000)
        self.assertEqual(reason["limit_cents"], 30_000)
        self.assertIn("$310.00 over the $300.00 per-order cap", reason["message"])

    def test_monthly_block_uses_agent_cap(self) -> None:
        blocked = evaluate(
            agent=agent(), lines=[line()], month_spent_cents=79_500
        )
        self.assertEqual(blocked["outcome"], "block")
        reason = blocked["reasons"][0]
        self.assertEqual(reason["rule"], "monthly_cap")
        self.assertEqual(reason["observed_cents"], 80_500)
        self.assertEqual(reason["limit_cents"], 80_000)

    def test_monthly_block_falls_back_to_workspace_budget(self) -> None:
        blocked = evaluate(
            agent=agent(monthly_cap_cents=None),
            lines=[line()],
            month_spent_cents=9_500,
            workspace_monthly_budget_cents=10_000,
        )
        self.assertEqual(blocked["outcome"], "block")
        self.assertEqual(blocked["reasons"][0]["limit_cents"], 10_000)

    def test_null_caps_are_skipped(self) -> None:
        decision = evaluate(
            agent=agent(
                monthly_cap_cents=None,
                per_order_cap_cents=None,
                per_unit_cap_cents=None,
                approval_threshold_cents=None,
            ),
            lines=[line(unit_cents=999_999, line_cents=999_999)],
        )
        self.assertEqual(decision["outcome"], "auto")
        self.assertEqual(decision["reasons"], [])

    # -- category overrides --------------------------------------------
    def test_category_override_loosens_per_unit_cap(self) -> None:
        decision = evaluate(
            agent=agent(
                per_unit_cap_cents=1_500,
                approval_threshold_cents=100_000,
                overrides=[{"category": "pantry", "kind": "per_unit_cap",
                            "value_cents": 2_000}],
            ),
            lines=[line(unit_cents=1_800, line_cents=1_800)],
        )
        self.assertEqual(decision["outcome"], "auto")

    def test_category_override_tightens_per_order_cap(self) -> None:
        blocked = evaluate(
            agent=agent(
                per_order_cap_cents=30_000,
                per_unit_cap_cents=None,
                approval_threshold_cents=None,
                overrides=[{"category": "pantry", "kind": "per_order_cap",
                            "value_cents": 5_000}],
            ),
            lines=[line(unit_cents=6_000, line_cents=6_000)],
        )
        self.assertEqual(blocked["outcome"], "block")
        reason = blocked["reasons"][0]
        self.assertEqual(reason["rule"], "per_order_cap")
        self.assertEqual(reason["observed_cents"], 6_000)
        self.assertEqual(reason["limit_cents"], 5_000)
        self.assertIn("pantry subtotal $60.00 over the $50.00 cap", reason["message"])

    def test_category_override_tightens_approval_threshold(self) -> None:
        over = evaluate(
            agent=agent(
                approval_threshold_cents=100_000,
                per_unit_cap_cents=None,
                overrides=[{"category": "pantry", "kind": "approval_threshold",
                            "value_cents": 5_000}],
            ),
            lines=[line(unit_cents=6_000, line_cents=6_000)],
        )
        self.assertEqual(over["outcome"], "review")
        reason = over["reasons"][0]
        self.assertEqual(reason["rule"], "approval_threshold")
        self.assertEqual(reason["observed_cents"], 6_000)
        self.assertEqual(reason["limit_cents"], 5_000)
        self.assertIn("pantry subtotal $60.00", reason["message"])

    def test_category_override_only_applies_to_its_category(self) -> None:
        over = evaluate(
            agent=agent(
                per_unit_cap_cents=1_500,
                approval_threshold_cents=100_000,
                categories=["pantry", "stationery"],
                vendors=["amazon.com"],
                overrides=[{"category": "pantry", "kind": "per_unit_cap",
                            "value_cents": 5_000}],
            ),
            lines=[
                line(unit_cents=4_000, line_cents=4_000),  # pantry: override 5000 -> ok
                line(sku="sku-2", title="Markers", category="stationery",
                     unit_cents=4_000, line_cents=4_000),  # default 1500 -> review
            ],
        )
        self.assertEqual(over["outcome"], "review")
        self.assertEqual(len(over["reasons"]), 1)
        self.assertIn("Markers", over["reasons"][0]["message"])

    def test_per_category_subtotal_override_adds_lines(self) -> None:
        blocked = evaluate(
            agent=agent(
                per_order_cap_cents=100_000,
                per_unit_cap_cents=None,
                approval_threshold_cents=100_000,
                overrides=[{"category": "pantry", "kind": "per_order_cap",
                            "value_cents": 5_000}],
            ),
            lines=[
                line(unit_cents=3_000, line_cents=3_000),
                line(sku="sku-2", unit_cents=3_000, line_cents=3_000),
            ],
        )
        self.assertEqual(blocked["outcome"], "block")
        self.assertEqual(blocked["reasons"][0]["observed_cents"], 6_000)

    # -- which rules are on --------------------------------------------
    def test_disabled_rules_are_skipped(self) -> None:
        decision = evaluate(
            agent=agent(
                rules=["approval_threshold"],
                vendors=[],
                categories=[],
                monthly_cap_cents=1,
                per_order_cap_cents=1,
                per_unit_cap_cents=1,
            ),
            lines=[line(vendor="staples.com", category="equipment", unit_cents=9_000,
                        line_cents=9_000)],
        )
        self.assertEqual(decision["outcome"], "review")
        self.assertEqual(rules(decision["reasons"]), ["approval_threshold"])

    def test_disabled_per_order_override_is_ignored(self) -> None:
        decision = evaluate(
            agent=agent(
                rules=["category_allowlist", "vendor_allowlist", "per_unit_cap",
                       "monthly_cap", "approval_threshold"],
                per_unit_cap_cents=None,
                approval_threshold_cents=None,
                overrides=[{"category": "pantry", "kind": "per_order_cap",
                            "value_cents": 1_000}],
            ),
            lines=[line(unit_cents=50_000, line_cents=50_000)],
        )
        self.assertEqual(decision["outcome"], "auto")

    # -- ordering and shape --------------------------------------------
    def test_block_reasons_precede_review_reasons(self) -> None:
        decision = evaluate(
            agent=agent(per_unit_cap_cents=1_500, approval_threshold_cents=None),
            lines=[line(vendor="staples.com", unit_cents=2_000, line_cents=2_000)],
        )
        self.assertEqual(decision["outcome"], "block")
        self.assertEqual(rules(decision["reasons"]),
                         ["vendor_allowlist", "per_unit_cap"])
        self.assertEqual(decision["reasons"][0]["severity"], "block")
        self.assertEqual(decision["reasons"][1]["severity"], "review")

    def test_block_order_is_stable(self) -> None:
        decision = evaluate(
            agent=agent(monthly_cap_cents=1, per_order_cap_cents=1,
                        categories=[], vendors=[]),
            lines=[line(category="equipment", vendor="staples.com")],
        )
        self.assertEqual(
            rules(decision["reasons"]),
            ["category_allowlist", "vendor_allowlist", "monthly_cap", "per_order_cap"],
        )

    def test_catalogue_unresolved_becomes_a_block_reason(self) -> None:
        decision = evaluate(agent=agent(), lines=[], unresolved=["unicorn tears"])
        self.assertEqual(decision["outcome"], "block")
        self.assertEqual(rules(decision["reasons"]), ["catalogue"])
        reason = decision["reasons"][0]
        self.assertIn("no product for: unicorn tears", reason["message"])
        self.assertEqual(reason["severity"], "block")

    def test_catalogue_reason_comes_before_rule_blocks(self) -> None:
        decision = evaluate(
            agent=agent(vendors=[]),
            lines=[line()],
            unresolved=["mystery box"],
        )
        self.assertEqual(decision["reasons"][0]["rule"], "catalogue")
        self.assertEqual(rules(decision["reasons"]), ["catalogue", "vendor_allowlist"])

    def test_reason_shape_is_complete(self) -> None:
        decision = evaluate(
            agent=agent(per_unit_cap_cents=100, per_order_cap_cents=100),
            lines=[line(unit_cents=200, line_cents=200)],
        )
        self.assertTrue(decision["reasons"])
        for reason in decision["reasons"]:
            self.assertEqual(
                set(reason),
                {"rule", "severity", "message", "observed_cents", "limit_cents", "unblock"},
            )
            self.assertIn(reason["severity"], ("block", "review"))
            self.assertTrue(reason["message"])

    def test_money_formatting_with_thousands(self) -> None:
        decision = evaluate(
            agent=agent(monthly_cap_cents=100_000, rules=["monthly_cap"]),
            lines=[line(unit_cents=123_456, line_cents=123_456)],
        )
        message = decision["reasons"][0]["message"]
        self.assertIn("$1,234.56", message)
        self.assertIn("$1,000.00", message)
        self.assertTrue(re.search(r"\$[\d,]+\.\d{2}", message))

    def test_duplicate_bad_categories_and_vendors_are_deduped(self) -> None:
        decision = evaluate(
            agent=agent(categories=[], vendors=[]),
            lines=[
                line(vendor="staples.com", category="equipment"),
                line(sku="sku-2", vendor="staples.com", category="equipment"),
            ],
        )
        self.assertEqual(rules(decision["reasons"]),
                         ["category_allowlist", "vendor_allowlist"])

    def test_rule_kinds_constant(self) -> None:
        self.assertEqual(
            RULE_KINDS,
            ("monthly_cap", "per_order_cap", "per_unit_cap", "approval_threshold",
             "vendor_allowlist", "category_allowlist"),
        )


if __name__ == "__main__":
    unittest.main()
