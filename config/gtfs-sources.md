# GTFS Feed Sources

Reference document for all GTFS feeds per region. Feeds already wired into `config-dev.yml` are marked **[wired]**. Feeds requiring registration, an API key, or manual URL investigation are marked **[needs setup]**.

> As of 2026-08, all `gtfs_feeds` entries in `config-dev.yml` are commented out pending confirmation that these sources are reliable — see the `# TODO: re-enable gtfs_feeds` markers in that file. The **[wired]** status below reflects the code path being wired, not that the feeds are currently active.

---

## west-europe

| Agency | Country | URL | Status | Notes |
|---|---|---|---|---|
| iRail (SNCB/NMBS) | Belgium | `https://gtfs.irail.be/nmbs/feed/gtfs.zip` | **[wired]** | National rail. Updated weekly. |
| De Lijn | Belgium | `https://opendata.delijn.be/gtfs/google_transit.zip` | **[wired]** | Bus/tram Flanders. |
| OV-API (all OV) | Netherlands | `https://gtfs.ovapi.nl/nl/gtfs-nl.zip` | **[wired]** | All Dutch public transport combined. Updated daily. |
| STIB/MIVB | Belgium | — | **[needs setup]** | Brussels metro/tram/bus. Portal: `https://stibmivb.opendatasoft.com/` — the direct GTFS zip URL requires navigating the API catalog to find the current file download link. |
| SNCF | France | — | **[needs setup]** | National rail. Register at `https://data.sncf.com/` and search for "GTFS" to get the download URL. |
| DB (Deutsche Bahn) | Germany | — | **[needs setup]** | National rail. Register at `https://data.deutschebahn.com/` — search for "GTFS" or "Fahrplandaten". |
| NS (national rail) | Netherlands | `https://gtfs.ovapi.nl/ns/gtfs-nl.zip` | Available (subset of all-OV) | Included in the all-OV feed above; add separately only if NS-only data is preferred. |

---

## scandinavia

| Agency | Country | URL | Status | Notes |
|---|---|---|---|---|
| Entur (all PT) | Norway | `https://storage.googleapis.com/marduk-production/outbound/gtfs/rb_norway-aggregated-gtfs.zip` | **[wired]** | All Norwegian public transport aggregated. Updated nightly. |
| Trafiklab | Sweden | — | **[needs setup]** | Per-operator feeds require a free API key from `https://www.trafiklab.se/`. Main feeds: GTFS Sverige 2 (national) and regional operator feeds. |
| Rejseplanen | Denmark | — | **[needs setup]** | Open data available at `https://www.rejseplanen.dk/` or via `https://help.rejseplanen.dk/hc/en-us` — check for direct GTFS download or API access. |
| Fintraffic | Finland | — | **[needs setup]** | Open data at `https://www.fintraffic.fi/en/opendata` — navigate to GTFS section for current download URL. |

---

## uk-iceland

| Agency | Country | URL | Status | Notes |
|---|---|---|---|---|
| ATOC / RSP | Great Britain | — | **[needs setup]** | National rail timetable data. Free registration required at `https://data.atoc.org/rail-industry-data`. Provides CIF format; GTFS conversion available via `https://github.com/planar/dtd2mysql` or similar. |
| Traveline / Bus Open Data | Great Britain | — | **[needs setup]** | Bus/coach data via `https://data.bus-data.dft.gov.uk/` (Bus Open Data Service). Free registration. GTFS format available. |

---

## central-europe

| Agency | Country | URL | Status | Notes |
|---|---|---|---|---|
| ÖBB | Austria | — | **[needs setup]** | National rail. Open data portal: `https://data.oebb.at/` — search for GTFS timetable data. |
| Swiss Open Transport | Switzerland | — | **[needs setup]** | `https://opentransportdata.swiss/en/dataset/timetable-2024-gtfs2020` — free registration required. Comprehensive national GTFS. |
| Various | Poland | — | **[needs setup]** | No national aggregator. Per-city feeds available: Warsaw (`https://mkuran.pl/gtfs/warsaw.zip`), Kraków, Gdańsk. Check `https://gtfs.guide/` for current URLs. |
| Various | Hungary | — | **[needs setup]** | BKK (Budapest) GTFS: `https://bkk.hu/gtfs/budapest_gtfs.zip` (verify URL is current). National MÁV rail: no open GTFS found. |

---

## south-europe

| Agency | Country | URL | Status | Notes |
|---|---|---|---|---|
| RENFE | Spain | — | **[needs setup]** | National rail. Open data at `https://data.renfe.com/` — search for GTFS or timetable download. |
| EMT / Metro de Madrid | Spain | — | **[needs setup]** | Madrid urban transport. Check `https://www.emtmadrid.es/` open data section. |
| Various | Italy | — | **[needs setup]** | No national GTFS aggregator. Check `https://gtfs.guide/` for regional Italian feeds. Trenitalia (national rail) does not publish open GTFS. |

---

## Notes on Update Frequency

- Wired feeds (iRail, De Lijn, OV-API, Entur) are re-downloaded on each pipeline run — no caching between runs unless the output file already exists (see `download_gtfs_feeds` skip logic).
- For production pipelines, consider pinning feed URLs to versioned snapshots where available to avoid unexpected format changes.

---

## Adding a New Feed

1. Verify the URL returns a valid GTFS zip (contains `stops.txt`, `stop_times.txt`, `trips.txt`)
2. Add the URL to the appropriate region's `gtfs_feeds` list in `config-dev.yml`
3. Update this file to mark the source as `[wired]`
