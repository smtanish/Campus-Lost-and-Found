from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import config
from db import reports, claims

TZ = config.TIMEZONE


def dashboard_stats():
    """All dashboard numbers in a single aggregation using $facet."""
    since = datetime.now(timezone.utc) - timedelta(days=14)
    is_lost = {"$cond": [{"$eq": ["$type", "lost"]}, 1, 0]}
    is_found = {"$cond": [{"$eq": ["$type", "found"]}, 1, 0]}
    pipeline = [{"$facet": {
        # lost/found counts per category
        "by_category": [
            {"$group": {"_id": "$category", "lost": {"$sum": is_lost}, "found": {"$sum": is_found}}},
            {"$sort": {"lost": -1}},
        ],
        # where things get lost the most
        "hotspots": [
            {"$match": {"type": "lost"}},
            {"$group": {"_id": "$location.name", "n": {"$sum": 1}, "geo": {"$first": "$location.geo"}}},
            {"$sort": {"n": -1}},
            {"$limit": 6},
        ],
        # open vs resolved for each type
        "status": [
            {"$group": {"_id": {"type": "$type", "status": "$status"}, "n": {"$sum": 1}}},
        ],
        # average hours from losing something to getting it back
        "recovery": [
            {"$match": {"type": "lost", "status": "resolved", "resolved_at": {"$exists": True}}},
            {"$group": {"_id": None, "avg_hours": {"$avg": {"$divide": [{"$subtract": ["$resolved_at", "$event_time"]}, 3600000]}}}},
        ],
        # reports per day for the last two weeks (dates in IST)
        "daily": [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": TZ}}, "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
        # what time of day items go missing
        "by_hour": [
            {"$match": {"type": "lost"}},
            {"$group": {"_id": {"$hour": {"date": "$event_time", "timezone": TZ}}, "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
        # flexible schema in numbers: which attribute fields are actually used
        "fields_used": [
            {"$project": {"kv": {"$objectToArray": {"$ifNull": ["$attributes", {}]}}}},
            {"$unwind": "$kv"},
            {"$group": {"_id": "$kv.k", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ],
    }}]
    s = next(reports.aggregate(pipeline))
    counts = {f"{d['_id']['type']}_{d['_id']['status']}": d["n"] for d in s["status"]}
    lost_total = counts.get("lost_open", 0) + counts.get("lost_resolved", 0)
    s["counts"] = counts
    s["lost_total"] = lost_total
    s["recovery_rate"] = round(100 * counts.get("lost_resolved", 0) / lost_total) if lost_total else 0
    s["avg_recovery_hours"] = round(s["recovery"][0]["avg_hours"], 1) if s["recovery"] else None
    # fill the gaps so the charts show every hour / every day, even the empty ones
    hours = {d["_id"]: d["n"] for d in s["by_hour"]}
    s["by_hour"] = [{"_id": h, "n": hours.get(h, 0)} for h in range(24)]
    days = {d["_id"]: d["n"] for d in s["daily"]}
    today = datetime.now(ZoneInfo(TZ))
    keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    s["daily"] = [{"_id": k, "n": days.get(k, 0)} for k in keys]
    return s


def pending_claims():
    # claims live in their own collection and point to a report, $lookup joins them
    return list(claims.aggregate([
        {"$match": {"status": "pending"}},
        {"$sort": {"created_at": 1}},
        {"$lookup": {"from": "reports", "localField": "report_id", "foreignField": "_id", "as": "item"}},
        {"$unwind": "$item"},
        {"$project": {"item.embedding": 0}},
    ]))
