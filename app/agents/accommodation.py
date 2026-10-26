"""Accommodation agent - where to stay (hotels/lodges/rooms) and where to eat
(restaurants/tiffin/cafes).

Two labeled data layers; every evidence item carries `source_tier`:
  1. internal_dataset - curated static JSON:
       data/accommodations/*.json (142 files, all 23 temples, booking links)
       data/restaurants/*.json  (120 files, 10 schema variants, verified dates)
  2. open-data - LIVE OpenStreetMap radius search via the Overpass API
       (keyless; multi-instance failover; cached; static data still answers
       when the live layer is down)

No vector RAG: ~360 small structured records -> deterministic filter + geo-rank
is the correct tool (RAG belongs to long unstructured text, see temple agent).
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.travel.resolver import (
    TravelResolverClarificationError,
    resolve_place,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACC_DIR = PROJECT_ROOT / "data" / "accommodations"
FOOD_DIR = PROJECT_ROOT / "data" / "restaurants"

MAX_STATIC = 5
MAX_OSM = 4

_STOPWORDS = {
    "the", "and", "for", "near", "from", "with", "please", "where", "what",
    "give", "some", "good", "best", "top", "any", "around", "into", "that",
    "this", "tell", "about", "need", "want", "looking", "show", "find",
    "hotel", "hotels", "room", "rooms", "stay", "lodging", "lodge",
    "restaurant", "restaurants", "cafe", "cafes", "food", "eat", "dining",
    "tiffin", "meals", "temple", "temples", "nearby", "available", "place",
}

_CONTENT_REF_RE = re.compile(r"\s*:contentReference\[[^\]]*\]\{[^}]*\}")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_hotels_cache: list[dict[str, Any]] | None = None
_restaurants_cache: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# static dataset loaders (tolerant: restaurants have 10 schema variants)
# ---------------------------------------------------------------------------

def _clean_text(value: Any, limit: int = 350) -> str:
    text = str(value or "").strip()
    text = _CONTENT_REF_RE.sub("", text)
    return text[:limit]


def _tokens(text: Any) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall(str(text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    }


def _unwrap(value: Any) -> Any:
    """{"value": x, "confidence": ...} -> x ; plain value -> value"""
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _norm_hotel(raw: dict, meta: dict) -> dict[str, Any]:
    booking = {
        str(k): str(v)
        for k, v in (raw.get("booking_links") or {}).items()
        if isinstance(v, str) and v.startswith("http")
    }
    ratings = raw.get("ratings") or {}
    record = {
        "temple_id": raw.get("temple_id") or meta.get("temple_id") or "",
        "temple_name": str(meta.get("associated_temple") or "").strip(),
        "name": str(raw.get("hotel_name") or "").strip(),
        "hotel_type": str(raw.get("hotel_type") or "").strip(),
        "address": str(raw.get("address") or "").strip(),
        "price_range": str(raw.get("price_range") or "").strip(),
        "rating": str(ratings.get("rating") or "").strip(),
        "rating_platform": str(ratings.get("platform") or "").strip(),
        "booking_links": booking,
        "amenities": [
            str(a).strip()
            for a in (raw.get("amenities") or [])
            if isinstance(a, str) and a.strip()
        ][:8],
        "check_in": str(raw.get("check_in_time") or "").strip(),
        "check_out": str(raw.get("check_out_time") or "").strip(),
        "phones": [
            str(p).strip()
            for p in (raw.get("contact_numbers") or [])
            if isinstance(p, str) and p.strip()
        ][:3],
        "map_link": str(raw.get("google_map_link") or "").strip(),
        "website": str(raw.get("official_website") or "").strip(),
        "about": _clean_text(raw.get("about")),
    }
    record["_haystack"] = _tokens(
        f"{record['temple_name']} {record['address']} {record['name']} "
        f"{record['hotel_type']}"
    )
    return record


def _norm_restaurant(raw: dict) -> dict[str, Any]:
    temple = raw.get("associatedTemple") or {}
    location = raw.get("location") or {}
    meta = raw.get("metadata") or {}

    name = raw.get("restaurantName")
    if isinstance(name, dict):
        name = name.get("value")
    name = str(name or "").strip()

    address = location.get("fullAddress")
    if not address:
        addr = location.get("address")
        address = addr.get("value") if isinstance(addr, dict) else addr
    address = str(address or location.get("landmark") or "").strip()

    # distance from temple: number | {"value": x} | walkingDistance dict
    travel = raw.get("travelInformation") or {}
    distance = _unwrap(travel.get("distanceFromTempleKm"))
    if distance is None:
        legacy = raw.get("distanceFromTemple") or {}
        walk = legacy.get("walkingDistance")
        distance = _unwrap(walk)
        if distance is None:
            distance = _unwrap(legacy.get("drivingDistance"))
    try:
        distance = round(float(distance), 2) if distance is not None else None
    except (TypeError, ValueError):
        distance = None

    prices = raw.get("priceInformation") or {}
    business = raw.get("businessInformation") or {}
    customer = raw.get("customerInformation") or {}
    dietary = raw.get("dietaryInformation") or {}
    food_type = raw.get("foodType") or {}
    contact = raw.get("contactInformation") or {}
    phone = contact.get("phoneNumber") or (raw.get("contact") or {}).get("phone")
    hours = raw.get("workingHours") or {}

    veg = dietary.get("vegetarian")
    if veg is None:
        veg = food_type.get("vegetarian")
    pure_veg = dietary.get("pureVegetarian")
    if pure_veg is None:
        pure_veg = food_type.get("pureVeg")

    rating = customer.get("googleRating")
    if rating is None:
        rating = business.get("rating")
    reviews = customer.get("totalReviews")
    if reviews is None:
        reviews = business.get("reviewCount")

    status = str(raw.get("businessStatus") or "").strip().lower()
    is_open = "closed" not in status

    hours_text = ""
    if hours.get("openingTime") and hours.get("closingTime"):
        days = hours.get("workingDays") or ""
        hours_text = f"{hours['openingTime']} - {hours['closingTime']}"
        if isinstance(days, str) and days:
            hours_text = f"{hours_text} ({days})"

    record = {
        "temple_id": str(temple.get("templeId") or "").strip(),
        "temple_name": str(temple.get("templeName") or "").strip(),
        "name": name,
        "city": str(location.get("city") or "").strip(),
        "address": address,
        "cuisine": [
            str(c).strip()
            for c in (
                (raw.get("basicInformation") or {}).get("cuisineType")
                or (raw.get("cuisine") or {}).get("types")
                or []
            )
            if isinstance(c, str) and c.strip()
        ][:6],
        "vegetarian": bool(veg) if veg is not None else None,
        "pure_veg": bool(pure_veg) if pure_veg is not None else None,
        "price_category": str(
            prices.get("priceCategory") or business.get("priceRange") or ""
        ).strip(),
        "average_cost_for_one": _unwrap(prices.get("averageCostForOne")),
        "average_cost_for_two": _unwrap(prices.get("averageCostForTwo")),
        "rating": rating,
        "reviews": reviews,
        "hours": hours_text,
        "phone": str(phone or "").strip(),
        "maps_link": str(raw.get("googleMapsLink") or "").strip(),
        "distance_km": distance,
        "open": is_open,
        "last_verified": str(meta.get("lastVerified") or "").strip(),
        "dataset_status": str(meta.get("status") or "").strip(),
    }
    record["_haystack"] = _tokens(
        f"{record['temple_name']} {record['city']} {record['address']} "
        f"{record['name']} {' '.join(record['cuisine'])}"
    )
    return record


def _load_hotels() -> list[dict[str, Any]]:
    global _hotels_cache
    if _hotels_cache is not None:
        return _hotels_cache

    records: list[dict[str, Any]] = []
    for path in sorted(ACC_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = {
            "associated_temple": data.get("associated_temple"),
            "temple_id": data.get("temple_id"),
        }
        for hotel in data.get("hotels") or []:
            record = _norm_hotel(hotel, meta)
            if record["name"]:
                records.append(record)
    _hotels_cache = records
    return records


def _load_restaurants() -> list[dict[str, Any]]:
    global _restaurants_cache
    if _restaurants_cache is not None:
        return _restaurants_cache

    records: list[dict[str, Any]] = []
    for path in sorted(FOOD_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        record = _norm_restaurant(data)
        if record["name"]:
            records.append(record)
    _restaurants_cache = records
    return records


# ---------------------------------------------------------------------------
# static selection: temple_ids (registry-resolved) -> keyword fallback
# ---------------------------------------------------------------------------

def _select_static(
    records: list[dict[str, Any]],
    *,
    question: str,
    entities: list[str],
    temple_ids: list[str] | None,
    kind: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []

    if temple_ids:
        wanted = set(temple_ids)
        selected = [r for r in records if r.get("temple_id") in wanted]

    if not selected:
        q_tokens = _tokens(question)
        if q_tokens:
            selected = [r for r in records if q_tokens & r["_haystack"]]

    if not selected:
        for mention in entities or []:
            m_tokens = _tokens(mention)
            if not m_tokens:
                continue
            selected = [r for r in records if m_tokens & r["_haystack"]]
            if selected:
                break

    if selected and kind == "restaurant":
        q_tokens = _tokens(question)
        if q_tokens & {"veg", "vegetarian", "pureveg", "pure"}:
            vegged = [r for r in selected if r.get("vegetarian")]
            if vegged:
                selected = vegged

    if kind == "restaurant":
        selected.sort(
            key=lambda r: (
                0 if r.get("open") else 1,
                r.get("distance_km") if r.get("distance_km") is not None else 999,
            )
        )
    return selected


# ---------------------------------------------------------------------------
# LIVE layer: OpenStreetMap Overpass radius search (keyless, failover)
# ---------------------------------------------------------------------------

_OVERPASS_FILTERS = {
    "hotel": ("tourism", r"^(hotel|guest_house|hostel|lodging|motel)$"),
    "restaurant": ("amenity", r"^(restaurant|cafe|fast_food)$"),
}


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(h)), 2)


def _overpass_search(
    kind: str,
    lat: float,
    lng: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """Returns (pois, error_message|None). Successes are cached (default TTL);
    failures are cached only briefly (short TTL) so the live layer retries."""
    from app.travel.cache import get_fact, set_fact

    radius = (
        settings.overpass_radius_m
        if kind == "hotel"
        else min(settings.overpass_radius_m, settings.overpass_food_radius_m)
    )

    cached = get_fact(
        "places", "overpass", kind, f"{lat:.3f}", f"{lng:.3f}", str(radius)
    )
    if cached and cached.get("pois") is not None:
        return list(cached.get("pois") or []), cached.get("error")

    tag_key, tag_value = _OVERPASS_FILTERS.get(kind, _OVERPASS_FILTERS["hotel"])
    query = (
        f'[out:json][timeout:8];'
        f'(nwr["{tag_key}"~"{tag_value}"]'
        f'(around:{radius},{lat},{lng}););'
        f'out tags center {settings.overpass_max_pois};'
    )

    # Parallel failover: instances are tried CONCURRENTLY so a global outage
    # costs one timeout (~overpass_timeout_s), not one timeout per instance.
    urls = [u.strip() for u in settings.overpass_urls.split(",") if u.strip()]
    last_error = "overpass unavailable"
    payload: dict | None = None

    def _attempt(base: str) -> dict:
        request = urllib.request.Request(
            base,
            data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
            headers={"User-Agent": "PilgrimAI/1.0 (nearby search)"},
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=settings.overpass_timeout_s
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    if urls:
        pool = ThreadPoolExecutor(max_workers=min(4, len(urls)))
        try:
            futures = {pool.submit(_attempt, base): base for base in urls}
            for future in as_completed(
                futures, timeout=settings.overpass_timeout_s + 2.0
            ):
                base = futures[future]
                try:
                    payload = future.result()
                    break
                except Exception as exc:  # noqa: BLE001 - try other instances
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("overpass instance failed (%s): %s", base, exc)
        except TimeoutError:  # no instance answered within the window
            last_error = "overpass timed out on all instances"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    if payload is None and not urls:
        last_error = "no overpass instances configured"

    if payload is None:
        set_fact(
            "places",
            "overpass",
            kind,
            f"{lat:.3f}",
            f"{lng:.3f}",
            str(radius),
            payload={"pois": [], "error": last_error},
            ttl=600,  # short TTL; live layer retries soon
        )
        return [], last_error

    pois: list[dict[str, Any]] = []
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        center = element.get("center") or {}
        p_lat = element.get("lat") if element.get("lat") is not None else center.get("lat")
        p_lng = element.get("lon") if element.get("lon") is not None else center.get("lon")
        if not name or p_lat is None or p_lng is None:
            continue
        address = " ".join(
            part
            for part in [
                str(tags.get("addr:housenumber") or "").strip(),
                str(tags.get("addr:street") or "").strip(),
                str(tags.get("addr:suburb") or "").strip(),
                str(tags.get("addr:city") or "").strip(),
            ]
            if part
        )
        pois.append(
            {
                "name": name,
                "category": str(tags.get(tag_key) or "").strip(),
                "cuisine": str(tags.get("cuisine") or "").strip(),
                "phone": str(
                    tags.get("phone") or tags.get("contact:phone") or ""
                ).strip(),
                "website": str(
                    tags.get("website") or tags.get("contact:website") or ""
                ).strip(),
                "address": address,
                "latitude": float(p_lat),
                "longitude": float(p_lng),
                "distance_km": _haversine_km(lat, lng, float(p_lat), float(p_lng)),
            }
        )

    pois.sort(key=lambda p: p["distance_km"])
    pois = pois[: settings.overpass_max_pois]
    set_fact(
        "places",
        "overpass",
        kind,
        f"{lat:.3f}",
        f"{lng:.3f}",
        str(radius),
        payload={"pois": pois, "error": None},
    )
    return pois, None


def _anchor(
    question: str,
    entities: list[str],
    temple_ids: list[str] | None,
):
    """Resolve a coordinate anchor for the live layer. Entity ids first (offline),
    then the first user mention. Ambiguous mentions raise for clarification."""
    candidates: list[Any] = []
    if temple_ids:
        candidates.append(temple_ids[0])
    for mention in entities or []:
        if mention and mention not in candidates:
            candidates.append(mention)

    for candidate in candidates[:3]:
        try:
            return resolve_place(candidate)
        except TravelResolverClarificationError:
            raise
        except Exception as exc:  # noqa: BLE001 - unresolvable -> static only
            logger.info("accommodation anchor failed for %r: %s", candidate, exc)
    return None


# ---------------------------------------------------------------------------
# evidence builders
# ---------------------------------------------------------------------------

def _hotel_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "hotel_dataset",
        "kind": "hotel",
        "source_tier": "internal_dataset",
        "name": record["name"],
        "hotel_type": record["hotel_type"],
        "temple_id": record["temple_id"],
        "temple_name": record["temple_name"],
        "address": record["address"],
        "price_range": record["price_range"],
        "rating": record["rating"],
        "rating_platform": record["rating_platform"],
        "booking_links": record["booking_links"],
        "amenities": record["amenities"],
        "check_in": record["check_in"],
        "check_out": record["check_out"],
        "phones": record["phones"],
        "map_link": record["map_link"],
        "about": record["about"],
    }


def _restaurant_evidence(record: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "source": "restaurant_dataset",
        "kind": "restaurant",
        "source_tier": "internal_dataset",
        "name": record["name"],
        "temple_id": record["temple_id"],
        "temple_name": record["temple_name"],
        "city": record["city"],
        "address": record["address"],
        "cuisine": record["cuisine"],
        "vegetarian": record["vegetarian"],
        "pure_veg": record["pure_veg"],
        "price_category": record["price_category"],
        "average_cost_for_one": record["average_cost_for_one"],
        "average_cost_for_two": record["average_cost_for_two"],
        "rating": record["rating"],
        "reviews": record["reviews"],
        "hours": record["hours"],
        "phone": record["phone"],
        "maps_link": record["maps_link"],
        "distance_km": record["distance_km"],
        "open": record["open"],
        "last_verified": record["last_verified"],
        "dataset_status": record["dataset_status"],
    }
    return item


def _osm_evidence(poi: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "kind": "poi",
        "source_tier": "open-data",
        "name": poi["name"],
        "category": poi["category"],
        "cuisine": poi["cuisine"],
        "phone": poi["phone"],
        "website": poi["website"],
        "address": poi["address"],
        "latitude": poi["latitude"],
        "longitude": poi["longitude"],
        "distance_km": poi["distance_km"],
    }


def _dedupe_against_static(osm_items: list[dict], static_names: list[str]) -> list[dict]:
    kept: list[dict] = []
    seen_names = [n.lower() for n in static_names]
    for item in osm_items:
        low = item["name"].lower()
        if any(
            SequenceMatcher(None, low, existing).ratio() > 0.72
            for existing in seen_names
        ):
            continue
        seen_names.append(low)
        kept.append(item)
    return kept


# ---------------------------------------------------------------------------
# agent entry point (registry contract)
# ---------------------------------------------------------------------------

def run(
    *,
    question: str = "",
    entities: list[str] | None = None,
    temple_ids: list[str] | None = None,
    kind: str = "hotel",
) -> dict:
    entities = entities or []
    noun = "hotels" if kind == "hotel" else "restaurants"

    try:
        records = _load_hotels() if kind == "hotel" else _load_restaurants()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "error": f"accommodation dataset unavailable: {type(exc).__name__}: {exc}",
            "evidence": [],
        }

    evidence: list[dict[str, Any]] = []

    # --- live anchor (for OSM radius search) ---
    try:
        anchor = _anchor(question, entities, temple_ids)
    except TravelResolverClarificationError:
        raise
    except Exception as exc:  # noqa: BLE001 - never fail the whole agent
        logger.info("accommodation anchor skipped: %s", exc)
        anchor = None

    if anchor is not None:
        evidence.append(
            {
                "source": "place_resolution",
                "kind": "place",
                "role": "anchor",
                "name": anchor.display_name,
                "latitude": anchor.coordinates.latitude,
                "longitude": anchor.coordinates.longitude,
                "resolved_via": anchor.source,
            }
        )

    # --- static curated layer ---
    selected = _select_static(
        records,
        question=question,
        entities=entities,
        temple_ids=temple_ids,
        kind=kind,
    )[:MAX_STATIC]

    if kind == "hotel":
        evidence.extend(_hotel_evidence(r) for r in selected)
    else:
        evidence.extend(_restaurant_evidence(r) for r in selected)

    # --- live OpenStreetMap layer ---
    osm_error: str | None = None
    if anchor is not None:
        try:
            pois, osm_error = _overpass_search(
                kind,
                anchor.coordinates.latitude,
                anchor.coordinates.longitude,
            )
        except Exception as exc:  # noqa: BLE001
            pois, osm_error = [], f"{type(exc).__name__}: {exc}"

        static_names = [r["name"] for r in selected]
        osm_items = _dedupe_against_static(
            [_osm_evidence(p, "osm_search") for p in pois],
            static_names,
        )[:MAX_OSM]
        evidence.extend(osm_items)

        if osm_error:
            evidence.append(
                {
                    "source": "osm_search",
                    "kind": "note",
                    "key": "osm_status",
                    "error": "Live OpenStreetMap search is unavailable right now.",
                    "hint": None,
                }
            )

    # --- honest no-data note ---
    has_results = any(
        item.get("kind") in {"hotel", "restaurant", "poi"} for item in evidence
    )
    if not has_results:
        template = (
            settings.hotel_search_link if kind == "hotel" else settings.food_search_link
        )
        try:
            query_text = (question or noun).strip() or noun
            hint = template.format(query=urllib.parse.quote_plus(query_text))
        except Exception:  # noqa: BLE001 - never fail on a template
            hint = None
        evidence.append(
            {
                "source": "accommodation" if kind == "hotel" else "restaurant",
                "kind": "note",
                "key": "no_data",
                "error": (
                    f"No {noun} found in the curated dataset or the live map "
                    "for this query."
                ),
                "hint": hint,
            }
        )

    return {"status": "COMPLETED", "evidence": evidence}