# Technical guide

This explains how the system works, from the data model to the matching logic, so you can explain and modify every part of it. Read it alongside the code; each section names the file where the thing lives.

## 1. The big picture

```
 Browser (HTML forms, Leaflet map)
        │  HTTP
        ▼
 Flask app (app.py) ──────────────── matching.py ── fastembed model (runs locally,
        │                              │              turns text into 384 numbers)
        │ PyMongo                      │
        ▼                              ▼
 MongoDB Atlas cluster  (database: lost_found)
   ├── reports     main collection, one document per lost/found report
   ├── claims      ownership claims, each points to a report
   ├── fs.files / fs.chunks   photos stored with GridFS
   └── indexes: text, 2dsphere, compound, Atlas Vector Search
```

There is no separate frontend project. Flask renders HTML templates on the server (Jinja), which keeps the project small. The only JavaScript is the category-specific form fields and the maps.

A request for a new report goes like this:

1. The form posts to `/report/lost` or `/report/found` (`new_report` in app.py).
2. The app keeps only the attribute fields that belong to the chosen category and were filled in.
3. The photo, if any, is saved to GridFS and its id stored in the report.
4. The report text is turned into an embedding vector (`embed(report_text(doc))`).
5. `insert_one` stores the document. MongoDB's `$jsonSchema` validator checks it first.
6. `find_matches(doc)` looks for similar reports of the opposite type and the page shows them.

## 2. Why a document database fits this problem

Lost-and-found data is irregular. A phone has a brand, model and colour. An ID card has a card type and a name. Keys have a count and a keychain. A relational design would need either one wide table full of NULLs, an EAV table (entity, attribute, value) that is painful to query, or one table per category that has to change every time a category is added.

In MongoDB each report is one document, and the varying part lives in an embedded `attributes` object whose shape depends on the category. Adding a new category means adding a few lines to `config.CATEGORIES`. No migration, no schema change.

The common fields are still enforced through schema validation (section 4), so "flexible" doesn't mean "anything goes".

The other reasons it fits: geospatial and full-text indexes are built in, aggregation pipelines cover the analytics, and Atlas adds vector search in the same database, so we don't need a separate vector store.

## 3. Data model

### `reports` collection

A lost report as stored:

```json
{
  "_id": ObjectId("..."),
  "type": "lost",
  "category": "Electronics",
  "title": "Black AirPods Pro",
  "description": "Black AirPods Pro in a black silicone case, lost while studying.",
  "event_time": ISODate("2026-10-08T10:30:00Z"),
  "location": {
    "name": "Central Library",
    "detail": "2nd floor reading hall",
    "geo": { "type": "Point", "coordinates": [75.5652, 26.8437] }
  },
  "attributes": {
    "device_type": "Earbuds", "brand": "Apple", "model": "AirPods Pro",
    "color": "Black", "has_case": "Yes"
  },
  "reporter": { "name": "Aarav Mehta", "email": "aarav.mehta@muj.manipal.edu" },
  "status": "open",
  "embedding": [0.0123, -0.0456, ... 384 numbers],
  "created_at": ISODate("2026-10-08T11:10:00Z")
}
```

A found ID card in the same collection has `"attributes": {"card_type": "Student ID", "name_on_card": "Riya Sharma"}`, and may have `private_note` (a detail only admins see) and `image_id` (the GridFS file).

Notes on the fields:

- `event_time` is when the item was lost/found, `created_at` is when the report was made. All times are stored in UTC and shown in IST.
- `location.geo` is GeoJSON. **Longitude comes first**, which is a common source of bugs.
- `embedding` is the vector used for semantic matching. Pages never send it to the browser (the `PUBLIC` projection in app.py excludes it and `private_note`).
- `status` is `open` or `resolved`. Resolved reports also get `resolved_at`.

### `claims` collection

```json
{
  "report_id": ObjectId("<found report>"),
  "lost_report_id": ObjectId("<claimant's lost report>") or null,
  "claimant": { "name": "...", "email": "..." },
  "proof": "There is a photo of my dog in the front slot",
  "status": "pending",
  "created_at": ISODate("...")
}
```

### Embedded vs referenced, and why

| Data | Choice | Reason |
|---|---|---|
| location, reporter, attributes | embedded in the report | Always read together with the report, small, owned by one report |
| claims | separate collection, referenced by `report_id` | Has its own lifecycle (pending → approved/rejected), the admin queries all pending claims across reports, and an item could attract many claims |
| photos | GridFS, referenced by `image_id` | Images can be large; GridFS splits them into 255 KB chunks so they never hit the 16 MB document limit, and normal report queries don't drag image bytes along |

The admin page joins claims back to their reports with `$lookup` (analytics.py, `pending_claims`).

## 4. Schema validation (db.py)

Both collections are created with a `$jsonSchema` validator. For reports it requires `type`, `category`, `title`, `description`, `event_time`, `location`, `reporter`, `status`, `created_at`, restricts `type` to lost/found, checks the email pattern, the GeoJSON shape and the title length. It deliberately says only `"attributes": {"bsonType": "object"}`, so any category's fields are accepted.

That is the balance to point out in a viva: the core is strict, the per-category part is flexible.

If an insert breaks the rules MongoDB raises a `WriteError` and the app shows a message. Demo query 2 in `queries/demo_queries.js` shows a rejected insert.

## 5. Indexes (db.py → `setup`)

| Index | Fields | Used by |
|---|---|---|
| `report_text` | text on title (weight 10), description (5), location.name (2) | keyword search, `$text` |
| `report_geo` | 2dsphere on `location.geo` | `$geoNear` and `$geoWithin` in search |
| `type_status_time` | `type, status, event_time desc` | home page lists, match candidates, filtered search |
| `category_status` | `category, status` | category filters |
| `status_created` (claims) | `status, created_at` | admin pending claims queue |
| `report_ref` (claims) | `report_id` | claims for one report |
| `report_vectors` (Atlas Search) | vector on `embedding` + filter fields | `$vectorSearch` |

The compound index follows the **ESR rule**: Equality fields first (`type`, `status`), then the Sort field, then Range. A query like "open found reports, newest first" uses it with no in-memory sort. Demo query 6 runs `explain()` to show IXSCAN instead of COLLSCAN.

A collection can only have one text index, which is why title, description and location are combined in one with weights.

## 6. How matching works (matching.py)

This is the "intelligence" part. When a report is created or opened, `find_matches(report)` runs in two steps.

### Step 1: find candidates with vector search

The report's text (title, description, attribute values) was turned into a 384-number vector by the model `BAAI/bge-small-en-v1.5`, which runs locally through the `fastembed` library (no API key, no internet after the first download). Texts with similar meaning get vectors that point in similar directions, so "Black AirPods Pro" and "wireless earbuds in a case" end up close even with no words in common. Closeness is measured by **cosine similarity** (1 = same direction, 0 = unrelated).

We ask MongoDB for the 25 nearest reports that also satisfy a filter:

- opposite type (a lost report only looks at found reports and vice versa)
- `status: "open"`
- `event_time` within a sensible window: a found item should be found after it was lost (one day of slack because people guess times), and at most 30 days apart

```python
{"$vectorSearch": {
    "index": "report_vectors", "path": "embedding", "queryVector": report["embedding"],
    "numCandidates": 250, "limit": 25,
    "filter": {"$and": [{"type": {"$eq": "found"}}, {"status": {"$eq": "open"}},
                        {"event_time": {"$gte": ...}}, {"event_time": {"$lte": ...}}]}}}
```

`$vectorSearch` uses an **approximate nearest neighbour** (ANN) index (HNSW graph). It looks at `numCandidates` points in the graph instead of every document, which is what makes it fast at scale. The trade-off is that it can occasionally miss a true neighbour; raising `numCandidates` improves recall at the cost of speed.

**Fallback:** if the Atlas vector index isn't ready (or you run on a local MongoDB without Atlas Search), `vector_search` loads the filtered candidates and computes cosine exactly in Python with numpy. This is exact k-NN: perfect recall but it scans every candidate. For a campus-sized dataset it's fast either way, and having both lets you compare exact vs approximate search, which is a good discussion point.

### Step 2: re-score each candidate with all the signals

Meaning alone isn't enough. Two black earbuds lost on opposite ends of campus a week apart are probably different items. `score_pair` combines five signals, each between 0 and 1:

| Signal | How it's computed | Weight |
|---|---|---|
| text | cosine similarity, stretched from the 0.50–0.90 range to 0–1 (bge rarely goes below 0.5 even for unrelated text) | 0.45 |
| location | `1 − distance / 800 m` using the haversine formula, 0 beyond 800 m | 0.20 |
| time | 1 if found within 6 h after loss, falling linearly to 0 at 7 days; 0 if found more than a day *before* the loss | 0.15 |
| category | 1 if same category, else 0 | 0.10 |
| attributes | of the attributes both reports filled in, the share that overlap (e.g. "White/black" vs "Black" share the word *black*). 0.5 if there is nothing to compare | 0.10 |

```
score = 0.45·text + 0.20·location + 0.15·time + 0.10·category + 0.10·attributes
```

Suggestions with a score of at least 0.55 are shown, best first, at most 5.

**Worked example** (the AirPods pair, assuming the model gives cosine 0.82):

- text: (0.82 − 0.50) / 0.40 = 0.80 → 0.45 × 0.80 = 0.36
- location: both at Central Library, 0 m → 1.0 → 0.20
- time: found 30 min after loss → 1.0 → 0.15
- category: both Electronics → 0.10
- attributes: shared keys device_type, color, has_case all overlap → 1.0 → 0.10
- **total 0.91**, shown as 91%

All of these numbers live in `config.py` (`WEIGHTS`, `MATCH_THRESHOLD`, `MAX_DISTANCE_M`, `MAX_GAP_HOURS`, `TEXT_SIM_LOW/HIGH`). Run `python matching.py` to print every open lost report with its suggestions and the score parts, then tune the numbers if you see too many or too few suggestions.

### Why geo isn't inside the vector query

`$vectorSearch` and `$geoNear` must each be the first stage of a pipeline, so they can't be combined in one query, and vector search filters don't support geo operators. So the vector search narrows things down to 25 candidates, and the distance is computed in Python for those. That's cheap because there are only 25.

## 7. Search page (app.py → `search`)

Three different MongoDB features depending on what's filled in:

- **Keywords** (default): `$text` query sorted by `textScore`. With a place selected it adds `$geoWithin` + `$centerSphere` (a circle of the chosen radius). `$text` can be combined with `$geoWithin` but not with `$near`, which is why `$geoWithin` is used here.
- **Meaning**: the query is embedded (with bge's "Represent this sentence for searching relevant passages:" prefix, which the model was trained with for short queries) and sent through the same `vector_search`. "earphones" finds the AirPods report.
- **Only a place, no text**: a `$geoNear` aggregation returns reports within the radius sorted by distance, and gives the distance in metres.

Type, category, status and date range are added as normal filters in every mode.

## 8. Claims and verification

A finder can enter a hidden detail (`private_note`) that is never shown publicly. When an owner claims the item they describe proof in their own words. The admin sees both side by side on the dashboard and approves or rejects.

Approving a claim (`review_claim`):
1. marks the claim approved,
2. rejects any other pending claims on the same item (`update_many`),
3. marks the found report, and the claimant's lost report if they claimed from it, as `resolved` with `resolved_at`.

`resolved_at − event_time` is what the dashboard uses for "average hours to recover".

Admin login is one shared password from `.env`, kept in the Flask session. That is enough for a project demo. A real deployment would use proper user accounts and roles (see section 11).

## 9. Dashboard aggregation (analytics.py)

`dashboard_stats` gets every number on the dashboard in **one** round trip with `$facet`, which runs several sub-pipelines over the same input:

| Facet | Stages | Shows |
|---|---|---|
| by_category | `$group` with `$sum` of `$cond` | lost vs found bars per category |
| hotspots | `$match` lost → `$group` by location → `$sort` → `$limit` | map circles and top places |
| status | `$group` by type + status | the counters and recovery rate |
| recovery | `$match` resolved → `$group` with `$avg` of `$subtract` dates | average hours to recover |
| daily | `$match` last 14 days → `$group` by `$dateToString` (IST) | reports per day |
| by_hour | `$group` by `$hour` (IST) | when items go missing |
| fields_used | `$objectToArray` → `$unwind` → `$group` | which attribute keys exist across all reports |

`fields_used` is a nice one to show: it treats the flexible `attributes` object as data, turning `{color: "Black", brand: "Apple"}` into `[{k: "color", v: "Black"}, {k: "brand", v: "Apple"}]`, unwinding it and counting keys. It's direct evidence of the varied schema.

Python then fills in hours and days with zero reports so the charts have no gaps.

## 10. File by file

- **config.py**: environment settings, campus places with coordinates, category → fields map, matching weights and thresholds. Most tweaks happen here.
- **db.py**: creates the client (`tz_aware=True` so dates come back with a timezone), defines the two `$jsonSchema` validators, `setup()` creates collections, validators and indexes, `create_vector_index()` asks Atlas for the vector index, `vector_index_ready()` checks whether it can be queried yet. Run `python db.py` on its own to (re)create indexes.
- **matching.py**: loads the model lazily, `embed` / `embed_query`, `vector_search` (Atlas or exact), the scoring helpers, `find_matches`.
- **analytics.py**: the `$facet` dashboard pipeline and the `$lookup` claims queue.
- **app.py**: all routes. Report form handling, item page, claim, GridFS image serving, search, admin login, claim review.
- **seed.py**: wipes the data, runs `setup()`, inserts 33 reports (9 lost/found pairs, unmatched ones, some already resolved) with times relative to today, and 2 pending claims.
- **templates/**: `base.html` (layout), `index.html`, `report_form.html` (builds category fields with a few lines of JS), `item.html` (details, map, matches, claim form), `search.html`, `admin.html`, `admin_login.html`, `_macros.html` (report card).
- **queries/demo_queries.js**: ten shell queries that show each concept on its own.

## 11. Scaling, replication and security

These aren't built as features, but they're likely viva questions and the design has answers:

- **Replication**: an Atlas M0 cluster is already a three-member replica set. Writes go to the primary and are copied to two secondaries; if the primary fails, an election promotes a secondary automatically. The app needs no changes because the `mongodb+srv` connection string lists the whole set.
- **Sharding**: one campus doesn't need it. For a multi-campus version, a shard key like `{campus_id: 1, _id: 1}` keeps each campus's reports together (match queries stay on one shard) while `_id` spreads load within a campus. A purely time-based key would send all new writes to one shard (a hot shard), which is why it's a bad choice here.
- **CAP / consistency**: with default settings reads go to the primary, so a user sees their report immediately after submitting. Atlas search indexes (including the vector index) are updated asynchronously, typically within a second, so a brand-new report can take a moment to appear in other people's vector matches. That's eventual consistency in practice.
- **Security**: Atlas enforces authentication (database user + password) and IP allow-listing, and encrypts data at rest and in transit (TLS) by default. The app user only needs `readWrite` on `lost_found`. Credentials live in `.env`, which is git-ignored.

## 12. Limitations and possible extensions

- Campus coordinates are approximate and places are picked from a list. Clicking on a map to drop a pin would be more precise.
- Admin auth is a single password. Real accounts would add per-user roles and let reporters edit or close their own reports.
- Matching uses text only. An image embedding model (e.g. CLIP) could compare photos too, using the same vector search approach.
- Emails could be sent when a strong match appears (MongoDB change streams would trigger this as soon as a report is inserted).
- Old resolved reports could be expired automatically with a TTL index.

## 13. Course outcome mapping

| Course outcome | Where it shows up |
|---|---|
| CO1: NoSQL principles, ACID/BASE, CAP, partitioning | Why a document store (section 2), consistency and sharding discussion (section 11) |
| CO2: CRUD, querying, indexing, schema design | Inserts/updates/finds in app.py, embedded document queries, 7 indexes, ESR compound index, embedded vs referenced design |
| CO3: aggregation, schema validation, application connectivity | `$facet` dashboard, `$lookup`, `$objectToArray`/`$unwind`, `$geoNear`, `$jsonSchema` validators, PyMongo app |
| CO4: architecture, replication, security, cloud | MongoDB Atlas (DBaaS), replica set, GridFS, auth and network access |
| CO5: vector databases, similarity, ANN | Embeddings, cosine similarity, Atlas `$vectorSearch` (ANN) vs exact fallback, semantic search |
