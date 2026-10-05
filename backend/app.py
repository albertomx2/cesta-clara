import csv
import io
import json
import os
import re
import subprocess
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from html import unescape
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import psycopg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]
GROCERY_BIN = os.getenv("GROCERY_BIN", "/usr/local/bin/grocery")
app = FastAPI(title="Cesta Clara API", version="0.2.0")


@contextmanager
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


STORES = [
    {"key": "mercadona", "name": "Mercadona", "color": "#16a36a", "live": True,
     "note": "Catálogo online · almacén Barcelona", "url": "https://tienda.mercadona.es/search-results?query={q}"},
    {"key": "carrefour", "name": "Carrefour", "color": "#2763d7", "live": True,
     "note": "Datos abiertos diarios · zona Granollers", "url": "https://www.carrefour.es/supermercado/?q={q}"},
    {"key": "lidl-es", "name": "Lidl", "color": "#f1c40f", "live": True,
     "note": "Catálogo y ofertas online de España", "url": "https://www.lidl.es/q/search?q={q}"},
    {"key": "aldi", "name": "Aldi", "color": "#23844e", "live": True,
     "note": "Catálogo publicado para Península", "url": "https://www.aldi.es/resultados-de-busqueda.html?query={q}"},
    {"key": "dia", "name": "DIA", "color": "#d71920", "live": True,
     "note": "Tienda online + respaldo diario · Granollers", "url": "https://www.dia.es/search?q={q}"},
    {"key": "alcampo", "name": "Alcampo", "color": "#ee4035", "live": True,
     "note": "Supermercado online Alcampo", "url": "https://www.compraonline.alcampo.es/search?q={q}"},
]
STORE_BY_KEY = {s["key"]: s for s in STORES}
DIRECT_KEYS = {"mercadona", "lidl-es", "aldi", "dia", "alcampo"}
FALLBACK_NAMES = {"mercadona": "mercadona", "carrefour": "carrefour", "lidl": "lidl-es",
                  "aldi": "aldi", "dia": "dia", "alcampo": "alcampo"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, color TEXT NOT NULL
);
ALTER TABLE stores ADD COLUMN IF NOT EXISTS adapter_key TEXT;
ALTER TABLE stores ADD COLUMN IF NOT EXISTS live BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE stores ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS stores_adapter_key_idx ON stores(adapter_key);
CREATE TABLE IF NOT EXISTS selected_stores (
  store_id INT PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS live_items (
  id SERIAL PRIMARY KEY,
  query TEXT UNIQUE NOT NULL,
  quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
  checked BOOLEAN NOT NULL DEFAULT FALSE,
  offers JSONB NOT NULL DEFAULT '[]'::jsonb,
  searched_at TIMESTAMPTZ
);
DROP TABLE IF EXISTS shopping_items;
DROP TABLE IF EXISTS prices;
DROP TABLE IF EXISTS products;
"""

_search_cache = {}
_cache_lock = Lock()
CACHE_SECONDS = 300
_promotions_cache = (0.0, [])
PROMOTIONS_CACHE_SECONDS = 3600


def canonical_unit(value: str) -> str:
    unit = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower().strip()
    if unit in {"kg", "kilo", "kilogramo", "kilogramos"}:
        return "kg"
    if unit in {"l", "lt", "litro", "litros"}:
        return "L"
    if unit in {"u", "ud", "uds", "unidad", "unidades", "each"}:
        return "u"
    return unit


def offer_sort_key(offer: dict, preferred_unit: str | None = None):
    """Rank comparable products by €/kg, €/L or €/unit; use pack price only as fallback."""
    unit = canonical_unit(offer.get("unit", ""))
    per_unit = float(offer.get("per_unit") or 0)
    price = float(offer.get("price") or 0)
    if per_unit > 0 and unit and (preferred_unit is None or unit == preferred_unit):
        return (0, per_unit, price)
    return (1, price, price)


def sort_offers(offers: list[dict]) -> tuple[list[dict], str | None]:
    unit_counts = {}
    for offer in offers:
        unit = canonical_unit(offer.get("unit", ""))
        if unit and float(offer.get("per_unit") or 0) > 0:
            offer["unit"] = unit
            unit_counts[unit] = unit_counts.get(unit, 0) + 1
    preferred = max(unit_counts, key=lambda unit: (unit_counts[unit], unit in {"kg", "L"}), default=None)
    return sorted(offers, key=lambda offer: offer_sort_key(offer, preferred)), preferred


def text_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1}


def current_promotions() -> list[dict]:
    """Load today's verified price drops. Failure is non-fatal: ordinary live prices still work."""
    global _promotions_cache
    now = time.time()
    with _cache_lock:
        if now - _promotions_cache[0] < PROMOTIONS_CACHE_SECONDS:
            return _promotions_cache[1]
    rows = []
    try:
        req = Request("https://chollosuper.com/datos/bajadas.csv",
                      headers={"User-Agent": "CestaClara/0.3 (+homelab personal project)"})
        with urlopen(req, timeout=12) as response:
            body = response.read(1_000_000).decode("utf-8-sig", "replace")
        for row in csv.DictReader(io.StringIO(body)):
            rows.append({
                "store_key": FALLBACK_NAMES.get((row.get("cadena") or "").strip().lower(),
                                                 (row.get("cadena") or "").strip().lower()),
                "product_name": row.get("producto") or "",
                "price": float(row.get("precio_eur") or 0),
                "previous_price": float(row.get("antes_eur") or 0),
                "discount_pct": int(float(row.get("descuento_pct") or 0)),
                "url": row.get("url") or "https://chollosuper.com/ofertas",
            })
    except (HTTPError, URLError, TimeoutError, ValueError):
        rows = []
    with _cache_lock:
        _promotions_cache = (now, rows)
    return rows


def add_promotion_data(offers: list[dict]) -> None:
    promotions = current_promotions()
    for offer in offers:
        offer["on_offer"] = False
        offer_tokens = text_tokens(offer.get("product_name", ""))
        candidates = []
        for promo in promotions:
            if promo["store_key"] != offer.get("store_key") or abs(promo["price"] - offer.get("price", 0)) > 0.011:
                continue
            promo_tokens = text_tokens(promo["product_name"])
            overlap = len(offer_tokens & promo_tokens) / max(1, min(len(offer_tokens), len(promo_tokens)))
            if overlap >= 0.55:
                candidates.append((overlap, promo))
        if candidates:
            promo = max(candidates, key=lambda match: match[0])[1]
            offer.update({"on_offer": True, "previous_price": promo["previous_price"],
                          "discount_pct": promo["discount_pct"], "promotion_url": promo["url"],
                          "promotion_text": "Bajada de precio detectada hoy"})


def candidate_unit_price(candidate: dict) -> tuple[float, str]:
    per_unit = float(candidate.get("pricePerUnit") or 0)
    unit = canonical_unit(candidate.get("unit", ""))
    if per_unit > 0 and unit:
        return per_unit, unit
    name = unicodedata.normalize("NFKD", candidate.get("name") or "").encode("ascii", "ignore").decode()
    sizes = re.findall(r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\b", name)
    if not sizes:
        return 0.0, ""
    amount_text, raw_unit = sizes[-1]
    amount = float(amount_text.replace(",", "."))
    raw_unit = raw_unit.lower()
    if raw_unit == "g":
        amount, unit = amount / 1000, "kg"
    elif raw_unit in {"ml", "cl"}:
        amount, unit = amount / (1000 if raw_unit == "ml" else 100), "L"
    else:
        unit = "kg" if raw_unit == "kg" else "L"
    return (float(candidate.get("price") or 0) / amount, unit) if amount > 0 else (0.0, "")


def query_store(key: str, clean_terms: list[str]) -> dict:
    try:
        proc = subprocess.run(
            [GROCERY_BIN, "batch", "-f", "-", "--store", key,
             "--candidates", "12", "--json"],
            input="\n".join(clean_terms) + "\n", text=True, capture_output=True,
            timeout=45, env={**os.environ, "GROCERY_ALDI_REGION": "pen"},
        )
    except subprocess.TimeoutExpired:
        return {"store": key, "error": "timeout", "rows": []}
    if proc.returncode != 0:
        return {"store": key, "error": "unavailable", "rows": []}
    try:
        return {"store": key, "rows": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"store": key, "error": "invalid", "rows": []}


def initialize():
    for attempt in range(20):
        try:
            with db() as conn:
                conn.execute(SCHEMA)
                for store in STORES:
                    conn.execute("""INSERT INTO stores(name,color,adapter_key,live,note)
                      VALUES(%s,%s,%s,%s,%s)
                      ON CONFLICT(name) DO UPDATE SET color=EXCLUDED.color,adapter_key=EXCLUDED.adapter_key,
                      live=EXCLUDED.live,note=EXCLUDED.note""",
                      (store["name"], store["color"], store["key"], store["live"], store["note"]))
                conn.execute("""DELETE FROM selected_stores USING stores
                  WHERE selected_stores.store_id=stores.id AND stores.live=FALSE""")
                conn.execute("""INSERT INTO selected_stores(store_id)
                  SELECT id FROM stores WHERE adapter_key IN ('carrefour','dia','alcampo') ON CONFLICT DO NOTHING""")
                conn.commit()
            return
        except psycopg.OperationalError:
            if attempt == 19:
                raise
            time.sleep(2)


@app.on_event("startup")
def startup():
    initialize()


def selected_live_stores():
    with db() as conn:
        return conn.execute("""SELECT s.id,s.name,s.color,s.adapter_key,s.note
          FROM stores s JOIN selected_stores ss ON ss.store_id=s.id
          WHERE s.live=TRUE ORDER BY s.id""").fetchall()


def source_url(store_key: str, term: str) -> str:
    store = STORE_BY_KEY.get(store_key, {})
    return store.get("url", "").format(q=quote_plus(term))


def slugify(term: str) -> str:
    normalized = unicodedata.normalize("NFKD", term).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def open_data_offers(term: str, store_meta: dict):
    """Read CholloSúper's CC BY 4.0 daily comparison page as a fallback."""
    page_url = f"https://chollosuper.com/mas-barato/{slugify(term)}"
    try:
        req = Request(page_url, headers={"User-Agent": "CestaClara/0.2 (+homelab personal project)"})
        with urlopen(req, timeout=12) as response:
            body = response.read(1_000_000).decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError):
        return []
    pattern = re.compile(
        r'<tr><td><i[^>]*></i>([^<]+)<br><span class="nota">(.*?)</span></td>'
        r'<td class="num"><b>([0-9,.]+) €</b></td>'
        r'<td class="num nota">([0-9,.]+) €/([^<]+)</td>', re.S)
    offers = []
    for store_name, product_name, price, per_unit, unit in pattern.findall(body):
        lookup = store_name.strip().lower()
        key = FALLBACK_NAMES.get(lookup)
        meta = store_meta.get(key)
        if not meta:
            continue
        offers.append({
            "store_id": meta["id"], "store_key": key, "store": meta["name"],
            "color": meta["color"], "product_id": None,
            "product_name": unescape(re.sub(r"<[^>]+>", "", product_name)).strip(),
            "price": float(price.replace(".", "").replace(",", ".")),
            "per_unit": float(per_unit.replace(".", "").replace(",", ".")),
            "unit": unescape(unit).strip(), "source_url": page_url,
            "source": "CholloSúper · datos diarios CC BY 4.0",
        })
    return offers


def compare_live(terms: list[str], store_rows: list[dict], use_cache: bool = True):
    clean_terms = [term.strip() for term in terms if term.strip()]
    if not clean_terms:
        return {}, datetime.now(timezone.utc).isoformat()
    keys = [row["adapter_key"] for row in store_rows if row["adapter_key"] in DIRECT_KEYS]
    cache_key = (tuple(clean_terms), tuple(keys))
    now = time.time()
    if use_cache:
        with _cache_lock:
            cached = _search_cache.get(cache_key)
            if cached and now - cached[0] < CACHE_SECONDS:
                return cached[1], cached[2]
    raw_results = []
    if keys:
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            futures = [pool.submit(query_store, key, clean_terms) for key in keys]
            raw_results = [future.result() for future in as_completed(futures)]
    store_meta = {row["adapter_key"]: row for row in store_rows}
    offers_by_term = {term: [] for term in clean_terms}
    errors_by_term = {term: [] for term in clean_terms}
    for result in raw_results:
        key = result.get("store")
        meta = store_meta.get(key)
        if not meta:
            continue
        if result.get("error"):
            for term in clean_terms:
                errors_by_term[term].append({"store": meta["name"], "reason": "Fuente temporalmente no disponible"})
            continue
        rows = {row.get("term"): row for row in result.get("rows", [])}
        for term in clean_terms:
            row = rows.get(term, {})
            passed = [candidate for candidate in row.get("candidates", []) if candidate.get("passed")]
            if not passed:
                errors_by_term[term].append({"store": meta["name"], "reason": "Sin coincidencia"})
                continue
            best_score = min(int(candidate.get("score") or 0) for candidate in passed)
            most_relevant = [candidate for candidate in passed if int(candidate.get("score") or 0) == best_score]
            enriched = []
            for candidate in most_relevant:
                per_unit, unit = candidate_unit_price(candidate)
                enriched.append((candidate, per_unit, unit))
            comparable = [entry for entry in enriched if entry[1] > 0 and entry[2]]
            chosen, per_unit, unit = min(
                comparable or enriched,
                key=lambda entry: entry[1] if entry[1] > 0 else float(entry[0].get("price") or 0),
            )
            offers_by_term[term].append({
                "store_id": meta["id"], "store_key": key, "store": meta["name"],
                "color": meta["color"], "product_id": chosen.get("id"),
                "product_name": chosen.get("name") or term,
                "price": float(chosen.get("price") or 0),
                "per_unit": per_unit,
                "unit": unit,
                "source_url": source_url(key, term),
                "source": "Tienda online",
            })
    for term in clean_terms:
        present = {offer["store_key"] for offer in offers_by_term[term]}
        for offer in open_data_offers(term, store_meta):
            if offer["store_key"] not in present:
                offers_by_term[term].append(offer)
                present.add(offer["store_key"])
        errors_by_term[term] = [error for error in errors_by_term[term]
                                if not any(offer["store"] == error["store"] for offer in offers_by_term[term])]
        add_promotion_data(offers_by_term[term])
    result = {}
    for term in clean_terms:
        ranked, comparison_unit = sort_offers(offers_by_term[term])
        result[term] = {"offers": ranked, "comparison_unit": comparison_unit,
                        "unavailable": errors_by_term[term]}
    searched_at = datetime.now(timezone.utc).isoformat()
    with _cache_lock:
        _search_cache[cache_key] = (now, result, searched_at)
    return result, searched_at


class ItemIn(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    quantity: int = Field(default=1, ge=1, le=99)


class ItemPatch(BaseModel):
    quantity: int | None = Field(default=None, ge=1, le=99)
    checked: bool | None = None


class StoreSelection(BaseModel):
    store_ids: list[int]


@app.get("/api/health")
def health():
    check = subprocess.run([GROCERY_BIN, "version"], capture_output=True, text=True, timeout=5)
    return {"status": "ok", "live_prices": check.returncode == 0}


@app.get("/api/stores")
def stores():
    with db() as conn:
        return conn.execute("""SELECT s.id,s.name,s.color,s.adapter_key,s.live,s.note,
          (ss.store_id IS NOT NULL) selected FROM stores s
          LEFT JOIN selected_stores ss ON ss.store_id=s.id ORDER BY s.id""").fetchall()


@app.put("/api/stores/selection")
def select_stores(payload: StoreSelection):
    if not payload.store_ids:
        raise HTTPException(400, "Selecciona al menos un supermercado disponible")
    with db() as conn:
        valid = conn.execute("SELECT id FROM stores WHERE live=TRUE AND id=ANY(%s)", (payload.store_ids,)).fetchall()
        valid_ids = [row["id"] for row in valid]
        if not valid_ids:
            raise HTTPException(400, "Selecciona al menos un supermercado disponible")
        conn.execute("DELETE FROM selected_stores")
        for store_id in valid_ids:
            conn.execute("INSERT INTO selected_stores(store_id) VALUES(%s)", (store_id,))
        conn.commit()
    with _cache_lock:
        _search_cache.clear()
    return {"ok": True}


@app.get("/api/live-search")
def live_search(q: str = Query(min_length=2, max_length=120)):
    store_rows = selected_live_stores()
    results, searched_at = compare_live([q], store_rows)
    data = results.get(q.strip(), {"offers": [], "unavailable": []})
    return {"query": q.strip(), "searched_at": searched_at, **data}


@app.post("/api/items", status_code=201)
def add_item(payload: ItemIn):
    query = " ".join(payload.query.split())
    store_rows = selected_live_stores()
    results, searched_at = compare_live([query], store_rows)
    offers = results.get(query, {}).get("offers", [])
    if not offers:
        raise HTTPException(404, "No se encontró este producto en los supermercados seleccionados")
    with db() as conn:
        conn.execute("""INSERT INTO live_items(query,quantity,offers,searched_at) VALUES(%s,%s,%s::jsonb,%s)
          ON CONFLICT(query) DO UPDATE SET quantity=live_items.quantity+EXCLUDED.quantity,
          offers=EXCLUDED.offers,searched_at=EXCLUDED.searched_at,checked=FALSE""",
          (query, payload.quantity, json.dumps(offers), searched_at))
        conn.commit()
    return {"ok": True}


@app.patch("/api/items/{item_id}")
def patch_item(item_id: int, payload: ItemPatch):
    updates, values = [], []
    if payload.quantity is not None:
        updates.append("quantity=%s"); values.append(payload.quantity)
    if payload.checked is not None:
        updates.append("checked=%s"); values.append(payload.checked)
    if updates:
        with db() as conn:
            values.append(item_id)
            conn.execute(f"UPDATE live_items SET {','.join(updates)} WHERE id=%s", values)
            conn.commit()
    return {"ok": True}


@app.delete("/api/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM live_items WHERE id=%s", (item_id,))
        conn.commit()


@app.post("/api/refresh")
def refresh_prices():
    with db() as conn:
        items = conn.execute("SELECT id,query FROM live_items ORDER BY id").fetchall()
    if not items:
        return {"ok": True, "updated": 0}
    store_rows = selected_live_stores()
    terms = [item["query"] for item in items]
    results, searched_at = compare_live(terms, store_rows, use_cache=False)
    with db() as conn:
        for item in items:
            offers = results.get(item["query"], {}).get("offers", [])
            conn.execute("UPDATE live_items SET offers=%s::jsonb,searched_at=%s WHERE id=%s",
                         (json.dumps(offers), searched_at, item["id"]))
        conn.commit()
    return {"ok": True, "updated": len(items), "searched_at": searched_at}


@app.get("/api/analysis")
def analysis():
    store_rows = selected_live_stores()
    with db() as conn:
        item_rows = conn.execute("SELECT id,query,quantity,checked,offers,searched_at FROM live_items ORDER BY id").fetchall()
    items = []
    store_totals = {s["id"]: {"total": 0.0, "coverage": 0} for s in store_rows}
    optimal_total = 0.0
    latest_update = None
    for row in item_rows:
        offers = row["offers"] if isinstance(row["offers"], list) else json.loads(row["offers"] or "[]")
        offers = [offer for offer in offers if any(s["id"] == offer.get("store_id") for s in store_rows)]
        offers, comparison_unit = sort_offers(offers)
        quantity = row["quantity"]
        cheapest = offers[0] if offers else None
        if cheapest:
            optimal_total += cheapest["price"] * quantity
        for offer in offers:
            if offer["store_id"] in store_totals:
                store_totals[offer["store_id"]]["total"] += offer["price"] * quantity
                store_totals[offer["store_id"]]["coverage"] += 1
        searched_at = row["searched_at"].isoformat() if row["searched_at"] else None
        if searched_at and (latest_update is None or searched_at > latest_update):
            latest_update = searched_at
        items.append({"id": row["id"], "name": row["query"], "query": row["query"],
                      "quantity": quantity, "checked": row["checked"], "prices": offers,
                      "cheapest": cheapest, "comparison_unit": comparison_unit,
                      "searched_at": searched_at})
    totals = []
    item_count = len(items)
    for store in store_rows:
        data = store_totals[store["id"]]
        totals.append({"id": store["id"], "name": store["name"], "color": store["color"],
                       "total": round(data["total"], 2), "coverage": data["coverage"],
                       "complete": item_count > 0 and data["coverage"] == item_count})
    totals.sort(key=lambda x: (not x["complete"], x["total"] if x["coverage"] else float("inf")))
    complete_totals = [total for total in totals if total["complete"]]
    single_best = complete_totals[0] if complete_totals else None
    savings = round(single_best["total"] - optimal_total, 2) if single_best else 0
    recommendations = []
    if items and savings >= 0.25:
        recommendations.append({"kind": "saving", "title": f"Ahorra {savings:.2f} € repartiendo la compra",
                                "text": "La combinación prioriza el menor precio por kg, litro o unidad comparable."})
    missing = sum(1 for item in items if not item["prices"])
    if missing:
        recommendations.append({"kind": "coverage", "title": f"{missing} producto(s) sin precio",
                                "text": "Prueba con un término más concreto o actualiza las fuentes."})
    if items and not recommendations:
        recommendations.append({"kind": "fresh", "title": "Precios consultados en las tiendas online",
                                "text": "Puedes actualizarlos antes de salir a comprar."})
    return {"items": items, "stores": store_rows, "store_totals": totals,
            "optimal_total": round(optimal_total, 2), "single_best": single_best,
            "savings": savings, "recommendations": recommendations,
            "latest_update": latest_update, "live_data": True, "nutrition": None,
            "price_note": "La opción destacada prioriza €/kg, €/l o €/unidad. Las ofertas se verifican con las bajadas diarias publicadas; los precios pueden variar por zona y tienda física."}
