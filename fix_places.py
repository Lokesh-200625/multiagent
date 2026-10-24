import json
from pathlib import Path

reg = json.loads(Path("data/registry/entity_registry.json").read_text(encoding="utf-8"))
places = json.loads(Path("data/geography/places.json").read_text(encoding="utf-8"))
existing = {p["canonical_name"].lower() for p in places["places"]}

for eid, ent in reg["entities"].items():
    if ent["entity_type"] != "temple": continue
    name = ent["canonical_name"].split(",")[0].strip()
    if name.lower() in existing: continue
    loc = ent.get("location") or {}
    if not loc.get("latitude"): continue
    places["places"].append({
        "place_id": eid, "canonical_name": name, "place_type": "temple",
        "aliases": ent.get("aliases", [])[:4],
        "location": {**loc, "latitude": float(loc["latitude"]), "longitude": float(loc["longitude"])}
    })

Path("data/geography/places.json").write_text(json.dumps(places, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Done! Now {len(places['places'])} places")