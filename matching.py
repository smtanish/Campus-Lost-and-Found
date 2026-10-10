import math
import re
from datetime import timedelta
import numpy as np
import config
from db import reports, vector_index_ready

_model = None


def get_model():
    # loaded lazily because the first load downloads the model (~70 MB, once)
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(config.EMBED_MODEL)
    return _model


def normalise(vec):
    vec = np.asarray(vec, dtype=float)
    return (vec / np.linalg.norm(vec)).tolist()


def embed(text):
    # for report descriptions (documents)
    return normalise(next(iter(get_model().embed([text]))))


def embed_query(text):
    # bge models expect this instruction in front of short search queries
    return embed("Represent this sentence for searching relevant passages: " + text)


def report_text(r):
    # the text we turn into a vector: title + description + attribute values.
    # category and yes/no answers are left out, they're scored separately and would
    # make every report in the same category look alike
    values = [str(v) for v in (r.get("attributes") or {}).values() if str(v).lower() not in ("yes", "no")]
    return f"{r['title']}. {r['description']}. {', '.join(values)}"


def vector_search(query_vec, filt, limit=20):
    """Nearest reports to query_vec that satisfy filt. Returns (doc, cosine) pairs.
    Uses Atlas $vectorSearch (approximate) when the index is ready, otherwise
    scores every candidate exactly in Python (fine for a few thousand reports)."""
    if vector_index_ready():
        pipeline = [
            {"$vectorSearch": {"index": config.VECTOR_INDEX, "path": "embedding", "queryVector": query_vec,
                               "numCandidates": limit * 10, "limit": limit}},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]
        if filt:
            pipeline[0]["$vectorSearch"]["filter"] = to_vector_filter(filt)
        # Atlas returns (1 + cosine) / 2 for cosine indexes, convert it back
        return [(d, 2 * d.pop("score") - 1) for d in reports.aggregate(pipeline)]
    q = np.asarray(query_vec)
    out = []
    for d in reports.find(filt):
        if d.get("embedding"):
            out.append((d, float(np.dot(q, np.asarray(d.pop("embedding"))))))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:limit]


def to_vector_filter(filt):
    # $vectorSearch filters are written as an $and of single-operator conditions
    conds = []
    for k, v in filt.items():
        if isinstance(v, dict):
            conds += [{k: {op: val}} for op, val in v.items()]
        else:
            conds.append({k: {"$eq": v}})
    return {"$and": conds} if len(conds) > 1 else conds[0]


def distance_m(a, b):
    # haversine distance between two GeoJSON points in metres
    (lng1, lat1), (lng2, lat2) = a["coordinates"], b["coordinates"]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def tokens(value):
    return set(re.findall(r"[a-z0-9]+", str(value).lower()))


def attribute_score(a, b):
    # compare only the attributes both reports filled in, e.g. colour vs colour
    shared = [k for k in a if k in b and a[k] and b[k]]
    if not shared:
        return 0.5, []  # nothing to compare, stay neutral
    hits = [k for k in shared if tokens(a[k]) & tokens(b[k])]
    return len(hits) / len(shared), hits


def time_score(lost_time, found_time):
    gap = (found_time - lost_time).total_seconds() / 3600
    if gap < -24:
        return 0.0, gap  # found well before it was lost, unlikely to be the same item
    if gap <= 6:
        return 1.0, gap  # small negative gaps are allowed because people guess times
    return max(0.0, 1 - (gap - 6) / (config.MAX_GAP_HOURS - 6)), gap


def stretch(cos):
    lo, hi = config.TEXT_SIM_LOW, config.TEXT_SIM_HIGH
    return min(1.0, max(0.0, (cos - lo) / (hi - lo)))


def score_pair(report, other, cos):
    lost, found = (report, other) if report["type"] == "lost" else (other, report)
    dist = distance_m(report["location"]["geo"], other["location"]["geo"])
    t, gap = time_score(lost["event_time"], found["event_time"])
    a, hits = attribute_score(report.get("attributes") or {}, other.get("attributes") or {})
    parts = {
        "text": stretch(cos),
        "location": max(0.0, 1 - dist / config.MAX_DISTANCE_M),
        "time": t,
        "category": 1.0 if report["category"] == other["category"] else 0.0,
        "attributes": a,
    }
    total = sum(config.WEIGHTS[k] * v for k, v in parts.items())
    return {"report": other, "score": round(total, 3), "parts": parts, "cosine": round(cos, 3),
            "distance_m": round(dist), "gap_hours": round(gap, 1), "attr_hits": hits}


def find_matches(report, limit=5):
    """Suggest reports of the opposite type that could be the same object."""
    if not report.get("embedding"):
        return []
    other = "found" if report["type"] == "lost" else "lost"
    window = timedelta(days=config.SEARCH_WINDOW_DAYS)
    t = report["event_time"]
    # a found item should be found after it was lost (with a day of slack)
    if report["type"] == "lost":
        when = {"$gte": t - timedelta(days=1), "$lte": t + window}
    else:
        when = {"$gte": t - window, "$lte": t + timedelta(days=1)}
    filt = {"type": other, "status": "open", "event_time": when}
    results = [score_pair(report, d, cos) for d, cos in vector_search(report["embedding"], filt, limit=25)]
    results = [r for r in results if r["score"] >= config.MATCH_THRESHOLD]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


if __name__ == "__main__":
    # quick check: print the suggestions for every open lost report
    for lost in reports.find({"type": "lost", "status": "open"}):
        print(f"\n{lost['title']} ({lost['location']['name']})")
        for m in find_matches(lost):
            parts = " ".join(f"{k}={v:.2f}" for k, v in m["parts"].items())
            print(f"  {m['score']:.2f}  {m['report']['title']}  cos={m['cosine']}  {parts}")
