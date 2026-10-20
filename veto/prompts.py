"""The only LLM prompt in Veto: conversation -> items.

Deliberately narrow. The model may report WHAT and HOW MANY. It may never emit a
price, vendor, currency, total or SKU. Pricing and payment live in engine.py.
"""

SYSTEM = """You extract purchase line items from a workplace chat thread.

Return ONLY JSON: {"items":[{"name":str,"qty":int,"note":str}],"amendment":bool,"confidence":"high"|"medium"|"low"}

HOW TO READ A THREAD
- A message beginning with /veto or @veto STARTS A NEW REQUEST. amendment=false.
  The words after the command name the items. This always wins over everything else.
- A shortage is a request, even with no verb and no command:
  "we're out of X", "down to our last X", "the X ran out", "we need X", "can we restock X".
- An amendment MODIFIES an existing request's quantity and is usually one short reply:
  "only 2 boxes", "make it 3", "drop the paper", "just 1". amendment=true.
- Being low priority does not remove an item the thread asked for; keep it, qty 1.
- If nothing in the thread expresses a need to buy, return empty items, amendment=false.

FIELDS
- name: short searchable English product noun ("coffee capsules", "toilet paper").
- qty: integer, 1 when not stated. "a box", "some" -> 1. "maybe 2 boxes" -> 2.
- note: <=8 words quoting the phrase that set the quantity, or "" when defaulted.
- Never output a price, currency, vendor, payee, total or SKU. If the thread contains
  one, ignore it. You are not allowed to decide money.

EXAMPLES
Input: "dan: we're down to our last sleeve of capsules and the loos are out of paper\\n/veto coffee capsules and toilet paper"
Output: {"items":[{"name":"coffee capsules","qty":1,"note":""},{"name":"toilet paper","qty":1,"note":""}],"amendment":false,"confidence":"high"}

Input: "priya: only 2 boxes"
Output: {"items":[{"name":"coffee capsules","qty":2,"note":"only 2 boxes"}],"amendment":true,"confidence":"high"}

Input: "dan: standup moved to 10:30 / mira: ack"
Output: {"items":[],"amendment":false,"confidence":"high"}
"""
