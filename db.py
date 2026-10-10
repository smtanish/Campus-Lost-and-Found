from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT, GEOSPHERE
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel
import gridfs
import config

client = MongoClient(config.MONGO_URI, tz_aware=True)
db = client[config.DB_NAME]
reports = db.reports
claims = db.claims
fs = gridfs.GridFS(db)  # stores uploaded photos in chunks inside MongoDB

# $jsonSchema validator: common fields are enforced, but "attributes" is left open
# so every category can store its own shape (flexible schema with a safety net)
REPORT_SCHEMA = {
    "bsonType": "object",
    "required": ["type", "category", "title", "description", "event_time", "location", "reporter", "status", "created_at"],
    "properties": {
        "type": {"enum": ["lost", "found"]},
        "category": {"enum": list(config.CATEGORIES)},
        "title": {"bsonType": "string", "minLength": 3, "maxLength": 100},
        "description": {"bsonType": "string", "maxLength": 2000},
        "event_time": {"bsonType": "date"},
        "location": {
            "bsonType": "object",
            "required": ["name", "geo"],
            "properties": {
                "name": {"bsonType": "string"},
                "detail": {"bsonType": "string"},
                "geo": {
                    "bsonType": "object",
                    "required": ["type", "coordinates"],
                    "properties": {
                        "type": {"enum": ["Point"]},
                        "coordinates": {"bsonType": "array", "minItems": 2, "maxItems": 2, "items": {"bsonType": "number"}},
                    },
                },
            },
        },
        "attributes": {"bsonType": "object"},
        "reporter": {
            "bsonType": "object",
            "required": ["name", "email"],
            "properties": {
                "name": {"bsonType": "string", "minLength": 2},
                "email": {"bsonType": "string", "pattern": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"},
                "phone": {"bsonType": "string"},
            },
        },
        "status": {"enum": ["open", "resolved"]},
        "embedding": {"bsonType": "array"},
        "image_id": {"bsonType": "objectId"},
        "private_note": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
        "resolved_at": {"bsonType": "date"},
    },
}

CLAIM_SCHEMA = {
    "bsonType": "object",
    "required": ["report_id", "claimant", "proof", "status", "created_at"],
    "properties": {
        "report_id": {"bsonType": "objectId"},
        "lost_report_id": {"bsonType": ["objectId", "null"]},
        "claimant": {"bsonType": "object", "required": ["name", "email"]},
        "proof": {"bsonType": "string", "minLength": 10},
        "status": {"enum": ["pending", "approved", "rejected"]},
        "created_at": {"bsonType": "date"},
    },
}


def apply_validator(name, schema):
    if name in db.list_collection_names():
        try:
            db.command("collMod", name, validator={"$jsonSchema": schema}, validationLevel="strict")
        except OperationFailure as e:
            print(f"could not update the validator on {name} ({e.code}), run seed.py to recreate it")
    else:
        db.create_collection(name, validator={"$jsonSchema": schema})


def setup():
    apply_validator("reports", REPORT_SCHEMA)
    apply_validator("claims", CLAIM_SCHEMA)
    # text index for keyword search, title matters most
    reports.create_index([("title", TEXT), ("description", TEXT), ("location.name", TEXT)],
                         weights={"title": 10, "description": 5, "location.name": 2}, name="report_text")
    # 2dsphere index for "near this place" queries
    reports.create_index([("location.geo", GEOSPHERE)], name="report_geo")
    # compound index following equality -> sort -> range (type, status, then time)
    reports.create_index([("type", ASCENDING), ("status", ASCENDING), ("event_time", DESCENDING)], name="type_status_time")
    reports.create_index([("category", ASCENDING), ("status", ASCENDING)], name="category_status")
    claims.create_index([("status", ASCENDING), ("created_at", ASCENDING)], name="status_created")
    claims.create_index("report_id", name="report_ref")
    create_vector_index()


def create_vector_index():
    # Atlas Vector Search index (ANN / HNSW under the hood) with filter fields
    definition = {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": config.EMBED_DIM, "similarity": "cosine"},
        {"type": "filter", "path": "type"},
        {"type": "filter", "path": "status"},
        {"type": "filter", "path": "category"},
        {"type": "filter", "path": "event_time"},
    ]}
    try:
        names = [i["name"] for i in reports.list_search_indexes()]
        if config.VECTOR_INDEX in names:
            print("vector search index already exists")
            return
        reports.create_search_index(SearchIndexModel(definition=definition, name=config.VECTOR_INDEX, type="vectorSearch"))
        print("vector search index requested, Atlas takes about a minute to build it")
    except OperationFailure as e:
        print("vector search index not available here, exact search will be used:", e.details.get("errmsg", e))


_vector_ready = False


def vector_index_ready():
    # once Atlas reports the index as queryable we remember it
    global _vector_ready
    if not _vector_ready:
        try:
            for idx in reports.list_search_indexes(config.VECTOR_INDEX):
                _vector_ready = idx.get("queryable", False)
        except OperationFailure:
            _vector_ready = False
    return _vector_ready


if __name__ == "__main__":
    setup()
    print("collections:", db.list_collection_names())
    for idx in reports.list_indexes():
        print(" index:", idx["name"])
