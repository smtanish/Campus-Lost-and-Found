# Campus Lost & Found Intelligence System

A web app where students report lost or found items on campus. Every new report is compared with the open reports of the opposite type, and the system suggests the ones that are probably the same object, using what the item is (meaning of the description), where, when, and its attributes.

Built with **MongoDB Atlas**, **Python (Flask + PyMongo)** and a small local embedding model for vector search.

For how everything works internally, read [TECHNICAL_README.md](TECHNICAL_README.md).

## Features

- Report lost / found items with category-specific fields (a laptop and an ID card store different attributes)
- Optional photo upload, stored in MongoDB with GridFS
- Automatic match suggestions with a score breakdown (text, location, time, category, attributes)
- Search by keywords (text index), by meaning (vector search), by place and radius (geospatial), category, type and date
- Claim flow: owner sends proof, admin compares it with the finder's hidden detail and approves or rejects
- Admin dashboard built from one `$facet` aggregation: hotspots map, category split, recovery rate, time-of-day pattern
- `$jsonSchema` validation on both collections

## Project structure

```
lost-and-found/
├── app.py              Flask routes (pages, forms, search, admin)
├── db.py               connection, schema validation, all indexes
├── matching.py         embeddings, vector search, match scoring
├── analytics.py        dashboard aggregation pipelines
├── config.py           settings, campus locations, categories, weights
├── seed.py             loads realistic sample data
├── queries/demo_queries.js   mongosh queries for the live demo
├── templates/          HTML pages (Jinja)
└── static/style.css
```

## Setup

### 1. Create a free MongoDB Atlas cluster

1. Sign up at https://www.mongodb.com/cloud/atlas and create a free **M0** cluster.
2. **Database Access** → add a database user with a password and the built-in role *Read and write to any database*.
3. **Network Access** → add your current IP address (for a demo you can allow `0.0.0.0/0`).
4. **Connect → Drivers → Python** and copy the connection string. It looks like
   `mongodb+srv://user:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`

### 2. Install and configure

Python 3.10 or newer.

```bash
cd lost-and-found
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and paste your connection string into `MONGO_URI` (replace `<password>`). You can also change `ADMIN_PASSWORD` there.

### 3. Load the sample data

```bash
python seed.py
```

This creates the collections with validation rules, all indexes (including the Atlas Vector Search index), and inserts 33 sample reports and 2 pending claims. The first run downloads the embedding model (about 70 MB) and caches it.

The vector index takes about a minute to build on Atlas. You can watch it in your cluster's search tab (called **Atlas Search** or **Search & Vector Search** depending on the Atlas version) until it says READY. Until then the app automatically falls back to exact similarity in Python, so it still works.

### 4. Run

```bash
python app.py
```

Open http://127.0.0.1:5000. Admin login is at `/admin` (default password `admin123`).

To see what the matcher thinks of every open lost report: `python matching.py`

## Demo walkthrough

1. Home page → open **Black AirPods Pro** → the found "Wireless earbuds in a case" is suggested with a high score, even though the words differ.
2. Click **This looks like mine** → send a claim mentioning the scratch on the case.
3. Log in as admin → the claim shows next to the finder's hidden detail → **Approve**. Both reports close and the recovery rate updates.
4. **Search**: try "earphones" with *Meaning* vs *Keywords*, then "Near Central Library within 200 m".
5. Report a new found item yourself and watch the suggestions appear.
6. Run the queries in `queries/demo_queries.js` in mongosh or the Atlas shell to show validation, indexes (`explain`), aggregation and `$vectorSearch` directly.

## Troubleshooting

- **Timeout / can't connect to Atlas**: check Network Access has your current IP. Some college Wi-Fi networks block port 27017, so try a mobile hotspot.
- **SSL certificate error on Mac**: run `Install Certificates.command` from your Python folder in Applications.
- **"vector search index not available"** printed by seed.py: create it by hand in Atlas → the search tab → Create Index → *Vector Search* → JSON editor, database `lost_found`, collection `reports`, name `report_vectors`:

  ```json
  {
    "fields": [
      {"type": "vector", "path": "embedding", "numDimensions": 384, "similarity": "cosine"},
      {"type": "filter", "path": "type"},
      {"type": "filter", "path": "status"},
      {"type": "filter", "path": "category"},
      {"type": "filter", "path": "event_time"}
    ]
  }
  ```

- **Maps are blank**: the maps load Leaflet and OpenStreetMap tiles from the internet, so they need a connection.
- **Campus locations look off on the map**: the coordinates in `config.py` → `LOCATIONS` are approximate. Adjust them (longitude first), then run `python seed.py` again.
