"""Channel3: the universal product catalogue the agent searches inside its rules.

Stdlib only, same as the rest of the runner. The agent never invents a product:
it sends the extracted item text to Channel3, and the offers that come back are
real products with real merchants and real buy links. The rules then decide
whether the cart may move.

The key lives in the environment (CHANNEL3_API_KEY); nothing here hardcodes it.
"""
from __future__ import annotations

from urllib.parse import urlencode

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CHANNEL3_BASE", "https://api.trychannel3.com")

# Channel3's taxonomy -> the charters' categories. The mapping reads the category
# slug and its full path, because a bare word in a title is not a category: a
# coffee pot, a glass milk bottle and a flour keeper all contain the food's name
# and none of them is food. Anything that does not map to a known category comes
# back as "general", which every charter refuses.
HOUSEHOLD_SLUGS = (
    "household-paper", "paper-towels", "toilet-paper", "toilet-tissue",
    "dish-detergent-soap", "dish-soap", "sponges", "garbage-bags", "trash-bags",
    "cleaning-supplies", "laundry-detergent", "household-supplies",
)
STATIONERY_SLUGS = (
    "office-supplies", "printer-copier-paper", "paper-products", "envelopes",
    "pens", "pencils", "notebooks", "markers", "staplers", "tape",
    "folders", "binders", "sticky-notes", "office-paper",
)
EQUIPMENT_SLUGS = (
    "keyboards", "mice-trackballs", "computer-monitors", "monitor-stands",
    "computer-monitor-stands-risers", "laptop-stands", "laptops", "webcams",
    "headphones", "headsets", "desk-lamps", "desks", "office-chairs", "chairs",
    "anti-fatigue-mats", "chair-mats", "floor-mats", "docking-stations",
    "label-makers", "printers", "scanners", "projectors",
)
FOOD_SLUGS = (
    "grocery", "groceries", "snacks", "cereal", "cereals", "beverages",
    "canned-goods", "pasta", "rice", "coffee", "tea", "baking", "meat", "seafood",
)
EQUIPMENT_ROOTS = ("electronics", "electronics-accessories", "cameras-optics",
                   "furniture", "computers", "office-equipment")
FOOD_ROOTS = ("food-beverages-tobacco", "food-items", "grocery", "pantry")


def _category_parts(product: dict) -> tuple[str, list[str]]:
    """(slug, path slugs) for a Channel3 product, lower-cased."""
    category = product.get("category") or {}
    slug = str(category.get("slug") or "").lower()
    path = [str(part.get("slug") or "").lower()
            for part in (category.get("path") or []) if isinstance(part, dict)]
    return slug, path


def _slug_matches(slug: str, hints: tuple[str, ...]) -> bool:
    return any(slug == hint or slug.startswith(hint + "-") or slug.endswith("-" + hint)
               for hint in hints)


def map_category(product: dict) -> str:
    """Which of the charters' categories a Channel3 product belongs to.

    The office-supplies root holds both paper and office furniture, so a root alone
    is not enough: mats and label makers are equipment even though they sit under it.
    """
    slug, path = _category_parts(product)
    in_path = set(path)

    # household consumables, matched on the specific leaf slug: "paper-towels" is
    # a pantry staple, "paint-sponges" is not
    for hint in HOUSEHOLD_SLUGS:
        if slug == hint or slug.startswith(hint + "-"):
            return "pantry"

    # actual food lives under the food root, never under home/kitchen/appliances
    if in_path & set(FOOD_ROOTS) or slug in FOOD_SLUGS:
        return "pantry"

    # office and computer equipment before the office-supplies root is decided
    if _slug_matches(slug, EQUIPMENT_SLUGS) or in_path & set(EQUIPMENT_ROOTS):
        return "equipment"

    # everything left under office supplies is writing, paper or filing
    if "office-supplies" in in_path:
        return "stationery"

    if _slug_matches(slug, STATIONERY_SLUGS):
        return "stationery"

    return "general"


class Channel3Error(RuntimeError):
    pass


def _cents(value) -> int:
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


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
    # "vertuo intenso coffee capsules" must not resolve to a Vertuo coffee machine.
    # A one-word request ("keyboard") has only one word to share, so the "two words
    # in common" test scales down instead of refusing every single-word item.
    must = max(words, key=len)
    needed = min(2, len(words))
    return must in title and len(shared) >= needed and len(shared) / len(words) >= 0.6


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
    category = product.get("category") or {}
    catalogue_category = str(category.get("title") or "").strip()
    if not catalogue_category:
        catalogue_category = str(category.get("slug") or "").replace("-", " ").strip()
    return {
        "sku": product.get("id") or "",
        "item": title,
        "qty": qty,
        "unit": unit,
        "vendor": offer.get("domain") or "",
        "category": map_category(product),
        "catalogue_category": catalogue_category,
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

    _category_cache: dict[str, list[dict]] = {}
    _brand_cache: dict[str, list[dict]] = {}

    def _get(self, path: str, params: dict) -> dict:
        url = BASE + path + "?" + urlencode(params)
        req = urllib.request.Request(
            url, headers={"x-api-key": self.api_key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise Channel3Error(f"Channel3 {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Channel3Error(f"Channel3 unreachable: {exc}") from exc

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
               website_ids: list[str] | None = None, category_ids: list[str] | None = None,
               brand_ids: list[str] | None = None,
               user_id: str | None = None) -> list[dict]:
        """Search the catalogue. `max_price_cents` is the agent's hard ceiling, so
        the rule set bounds the search itself rather than only judging the result.
        Category and brand filters come from the categories/brands lookups below."""
        key = (query.strip().lower(), limit, max_price_cents, tuple(website_ids or ()),
               tuple(category_ids or ()), tuple(brand_ids or ()))
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
        if category_ids:
            filters["category_ids"] = list(category_ids)
        if brand_ids:
            filters["brand_ids"] = list(brand_ids)
        if filters:
            payload["filters"] = filters
        if user_id:
            payload["user_id"] = user_id
        data = self._post("/v1/search", payload)
        products = data.get("products") or data.get("data") or []
        self._cache[key] = products
        return products

    def categories(self, query: str, limit: int = 6) -> list[dict]:
        """Catalogue categories matching a product noun. Slugs are stable; unknown
        slugs are silently dropped by search, so never guess one."""
        phrase = query.strip().lower()
        if not phrase:
            return []
        if phrase in self._category_cache:
            return self._category_cache[phrase]
        data = self._get("/v1/categories/search",
                         {"query": query, "limit": max(1, min(20, limit))})
        found = data.get("categories") or data.get("data") or []
        self._category_cache[phrase] = found
        return found

    def brands(self, query: str, limit: int = 10) -> list[dict]:
        """Brands matching a named brand. Channel3 keeps several ids per brand name,
        so callers must match on the name before filtering."""
        phrase = query.strip().lower()
        if not phrase:
            return []
        if phrase in self._brand_cache:
            return self._brand_cache[phrase]
        data = self._get("/v1/brands/search",
                         {"query": query, "limit": max(1, min(20, limit))})
        found = data.get("brands") or data.get("data") or []
        self._brand_cache[phrase] = found
        return found


def timed(fn, *args, **kwargs):
    """Small helper for the demo log: returns (result, seconds)."""
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, round(time.perf_counter() - t0, 2)
