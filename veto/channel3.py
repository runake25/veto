"""Channel3: the universal product catalogue the agent searches inside its rules.

Stdlib only, same as the rest of the runner. The agent never invents a product:
it sends the extracted item text to Channel3, and the offers that come back are
real products with real merchants and real buy links. The rules then decide
whether the cart may move.

The key lives in the environment (CHANNEL3_API_KEY); nothing here hardcodes it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CHANNEL3_BASE", "https://api.trychannel3.com")

# Channel3's taxonomy -> the charters' categories. A product that maps to no
# known category comes back as "general", which the category rule refuses.
CATEGORY_HINTS = [
    ("equipment", ("keyboard", "mouses", "mouse", "monitor", "desk", "chair", "stand",
                   "mat", "dock", "ergonomic", "laptop", "computer", "electronic",
                   "furniture", "headphone", "printer")),
    ("pantry", ("toilet", "tissue", "household-paper", "paper-towel", "coffee", "tea",
                "food", "snack", "beverage", "clean", "soap", "dish", "pantry",
                "grocery", "household-supplies")),
    ("stationery", ("pen", "pencil", "marker", "notebook", "stationery", "office-supplies",
                    "writing", "copy-paper", "printer-paper", "sticky", "envelope")),
]


class Channel3Error(RuntimeError):
    pass


def _cents(value) -> int:
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def map_category(product: dict) -> str:
    """Which of the charters' categories a Channel3 product belongs to."""
    cat = product.get("category") or {}
    hay = " ".join([str(cat.get("slug") or ""), str(cat.get("title") or "")]
                   + [str(p.get("slug") or "") for p in (cat.get("path") or [])]).lower()
    slug = str(cat.get("slug") or "").lower()
    for name, hints in CATEGORY_HINTS:
        if any(h in slug for h in hints):
            return name
    for name, hints in CATEGORY_HINTS:
        if any(h in hay for h in hints):
            return name
    return "general"


def best_offer(product: dict) -> dict | None:
    """The offer a buyer would actually take: in stock, cheapest."""
    offers = [o for o in (product.get("offers") or []) if o.get("url") and o.get("price")]
    if not offers:
        return None
    in_stock = [o for o in offers if str(o.get("availability", "")).lower() in ("instock", "in_stock")]
    pool = in_stock or offers
    return min(pool, key=lambda o: _cents((o.get("price") or {}).get("price")))


def rank(query: str, products: list[dict]) -> list[dict]:
    """Order the results the way a buyer would: how well the title answers the
    request first, then the cheaper offer. A search for "2 boxes of capsules"
    should not land on the 100-count case."""
    stop = {"a", "an", "the", "of", "for", "and", "from", "with", "to", "in", "on",
            "box", "boxes", "pack", "packs", "count", "ct", "roll", "rolls", "each",
            "some", "please", "get", "buy", "order", "need", "new", "our", "the"}
    words = {w for w in re.findall(r"[a-z]+", query.lower()) if w not in stop and len(w) > 2}
    if not words:
        words = set(re.findall(r"[a-z]+", query.lower()))

    def score(product: dict) -> tuple:
        title = set(re.findall(r"[a-z0-9]+", str(product.get("title") or "").lower()))
        overlap = len(words & title) / max(1, len(words))
        offer = best_offer(product) or {}
        price = _cents((offer.get("price") or {}).get("price")) or 10 ** 9
        return (-round(overlap, 3), price)

    return sorted(products, key=score)


def relevant(query: str, product: dict) -> bool:
    """Would a buyer accept this as the thing they asked for? A cart for "vertuo
    intenso capsules" must not come back with a coffee machine because the word
    "vertuo" appeared in both titles."""
    stop = {"a", "an", "the", "of", "for", "and", "from", "with", "to", "in", "on",
            "box", "boxes", "pack", "packs", "count", "ct", "roll", "rolls", "each"}
    words = {w for w in re.findall(r"[a-z]+", query.lower()) if w not in stop and len(w) > 2}
    if not words:
        return True
    title = set(re.findall(r"[a-z]+", str(product.get("title") or "").lower()))
    shared = words & title
    # the most specific word must be there, and most of the request must be answered:
    # "vertuo intenso coffee capsules" must not resolve to a Vertuo coffee machine
    must = max(words, key=len)
    return must in title and len(shared) >= 2 and len(shared) / len(words) >= 0.6


def to_line(product: dict, qty: int = 1) -> dict | None:
    """Channel3 product -> one cart line in Veto's shape."""
    offer = best_offer(product)
    if not offer:
        return None
    unit = _cents((offer.get("price") or {}).get("price"))
    if unit <= 0:
        return None
    title = str(product.get("title") or "").strip()
    if len(title) > 72:
        title = title[:69].rstrip() + "…"
    images = product.get("images") or []
    image = ""
    if images:
        main = next((i for i in images if i.get("is_main_image")), images[0])
        image = main.get("url") or ""
    brand = (product.get("brands") or [{}])[0].get("name") or ""
    return {
        "sku": product.get("id") or "",
        "item": title,
        "qty": qty,
        "unit": unit,
        "vendor": offer.get("domain") or "",
        "category": map_category(product),
        "line": unit * qty,
        "url": offer.get("url") or "",
        "image": image,
        "brand": brand,
        "source": "channel3",
    }


class Channel3:
    def __init__(self, api_key: str | None = None, country: str | None = None,
                 currency: str | None = None, timeout: float = 15.0) -> None:
        self.api_key = (api_key or os.environ.get("CHANNEL3_API_KEY") or "").strip()
        self.country = country or os.environ.get("CHANNEL3_COUNTRY") or "US"
        self.currency = currency or os.environ.get("CHANNEL3_CURRENCY") or "USD"
        self.timeout = timeout
        self._cache: dict[tuple, list] = {}

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode(),
            headers={"x-api-key": self.api_key, "Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise Channel3Error(f"Channel3 {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Channel3Error(f"Channel3 unreachable: {exc}") from exc

    def search(self, query: str, limit: int = 6, max_price_cents: int | None = None,
               website_ids: list[str] | None = None, user_id: str | None = None) -> list[dict]:
        """Search the catalogue. `max_price_cents` is the agent's hard ceiling, so
        the rule set bounds the search itself rather than only judging the result."""
        key = (query.strip().lower(), limit, max_price_cents, tuple(website_ids or ()))
        if key in self._cache:
            return self._cache[key]
        payload: dict = {
            "query": query,
            "limit": max(1, min(20, limit)),
            "config": {"country": self.country, "currency": self.currency},
        }
        filters: dict = {}
        if max_price_cents:
            filters["price"] = {"max_price": round(max_price_cents / 100, 2)}
        if website_ids:
            filters["website_ids"] = list(website_ids)
        if filters:
            payload["filters"] = filters
        if user_id:
            payload["user_id"] = user_id
        data = self._post("/v1/search", payload)
        products = data.get("products") or data.get("data") or []
        self._cache[key] = products
        return products


def timed(fn, *args, **kwargs):
    """Small helper for the demo log: returns (result, seconds)."""
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, round(time.perf_counter() - t0, 2)
