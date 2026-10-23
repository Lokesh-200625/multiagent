# PilgrimAI

PilgrimAI is a Telangana pilgrimage assistant with temple retrieval, road
routing, and scheduled GTFS transit journeys.

## Runtime configuration

Set the required values in an environment file outside source control:

- `DATABASE_URL` for the PostgreSQL transit schema.
- `REDIS_HOST` and `REDIS_PORT` for sessions and caches.
- `ORS_API_KEY` when OpenRouteService is enabled.
- `GROQ_API_KEY_1` or `GROQ_API_KEY_2` for query understanding.

OSRM is the fallback road provider. Nominatim is used for place resolution.
All external calls have bounded timeouts. Missing provider data is reported as
unavailable rather than treated as zero.

## Transit feeds

Transit results are scheduled GTFS timetable results, not live vehicle
tracking. Coverage depends on the currently active provider feed. Fare data is
reported unavailable unless a journey-specific fare is available.

Refresh a feed manually after downloading it from the official provider:

```powershell
python -m app.scripts.refresh_transit --provider tgsrtc --file C:\path\feed.zip
```

The ingestion transaction validates the feed before activation and preserves
the previous active feed if validation fails. Do not download feeds on every
application startup.

## Running

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Run the test suite with:

```powershell
python -m pytest -q
```

## Known limitations

- Road bike requests use the provider's standard driving profile and are not
  motorcycle-specific.
- Toll data is unavailable unless a provider returns verified route-specific
  toll information.
- Transit searches currently return direct and one-transfer scheduled
  journeys; live delays and cancellations are not included.
- TGSRTC feed coverage must be checked against the active feed and is not
  assumed to cover every Telangana town.
