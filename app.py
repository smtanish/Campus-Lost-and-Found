from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo
from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, Response
from pymongo.errors import WriteError
import gridfs
import config
from db import reports, claims, fs
from matching import embed, embed_query, report_text, find_matches, vector_search, distance_m
from analytics import dashboard_stats, pending_claims

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # photos up to 4 MB
IST = ZoneInfo(config.TIMEZONE)
PUBLIC = {"embedding": 0, "private_note": 0}  # never send these to normal pages


@app.template_filter("ist")
def ist(dt, fmt="%d %b %Y, %I:%M %p"):
    return dt.astimezone(IST).strftime(fmt) if dt else ""


@app.context_processor
def globals_for_templates():
    return {"categories": config.CATEGORIES, "locations": config.LOCATIONS, "is_admin": session.get("admin", False)}


def oid(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        abort(404)


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def parse_local_time(value):
    # the form sends "2026-10-02T16:00" in IST, we store UTC
    return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=IST).astimezone(timezone.utc)


@app.route("/")
def home():
    recent_lost = list(reports.find({"type": "lost", "status": "open"}, PUBLIC).sort("created_at", -1).limit(6))
    recent_found = list(reports.find({"type": "found", "status": "open"}, PUBLIC).sort("created_at", -1).limit(6))
    counts = {
        "lost": reports.count_documents({"type": "lost", "status": "open"}),
        "found": reports.count_documents({"type": "found", "status": "open"}),
        "resolved": reports.count_documents({"status": "resolved"}),
    }
    return render_template("index.html", recent_lost=recent_lost, recent_found=recent_found, counts=counts)


@app.route("/report/<kind>", methods=["GET", "POST"])
def new_report(kind):
    if kind not in ("lost", "found"):
        abort(404)
    if request.method == "GET":
        return render_template("report_form.html", kind=kind, form={})
    f = request.form
    category = f.get("category", "")
    place = f.get("location", "")
    errors = []
    if category not in config.CATEGORIES:
        errors.append("Pick a category.")
    if place not in config.LOCATIONS:
        errors.append("Pick a location.")
    try:
        event_time = parse_local_time(f.get("event_time", ""))
        if event_time > datetime.now(timezone.utc):
            errors.append("The date and time can't be in the future.")
    except ValueError:
        errors.append("Enter a valid date and time.")
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("report_form.html", kind=kind, form=f), 400
    # only keep the fields that belong to this category and were filled in
    attributes = {}
    for key, _, _ in config.CATEGORIES[category]:
        value = f.get(f"attr_{key}", "").strip()
        if value:
            attributes[key] = value
    doc = {
        "type": kind,
        "category": category,
        "title": f.get("title", "").strip(),
        "description": f.get("description", "").strip(),
        "event_time": event_time,
        "location": {"name": place, "geo": {"type": "Point", "coordinates": list(config.LOCATIONS[place])}},
        "attributes": attributes,
        "reporter": {"name": f.get("name", "").strip(), "email": f.get("email", "").strip().lower()},
        "status": "open",
        "created_at": datetime.now(timezone.utc),
    }
    if f.get("location_detail", "").strip():
        doc["location"]["detail"] = f["location_detail"].strip()
    if f.get("phone", "").strip():
        doc["reporter"]["phone"] = f["phone"].strip()
    if kind == "found" and f.get("private_note", "").strip():
        doc["private_note"] = f["private_note"].strip()
    photo = request.files.get("photo")
    if photo and photo.filename:
        if not (photo.mimetype or "").startswith("image/"):
            flash("The photo must be an image file.", "error")
            return render_template("report_form.html", kind=kind, form=f), 400
        doc["image_id"] = fs.put(photo.read(), filename=photo.filename, content_type=photo.mimetype)
    doc["embedding"] = embed(report_text(doc))
    try:
        new_id = reports.insert_one(doc).inserted_id
    except WriteError:
        # the $jsonSchema validator rejected it (e.g. title too short, bad email)
        if doc.get("image_id"):
            fs.delete(doc["image_id"])
        flash("Some details look invalid. Check the title (3+ characters) and email.", "error")
        return render_template("report_form.html", kind=kind, form=f), 400
    matches = find_matches(doc)
    if matches:
        flash(f"We found {len(matches)} possible match(es) for your report below.", "ok")
    else:
        flash("Report saved. No likely matches yet, check back later.", "ok")
    return redirect(url_for("item", item_id=new_id))


@app.route("/item/<item_id>")
def item(item_id):
    doc = reports.find_one({"_id": oid(item_id)})
    if not doc:
        abort(404)
    matches = find_matches(doc) if doc["status"] == "open" else []
    lost_ref = request.args.get("lost")  # set when someone claims from their lost report
    lost_doc = reports.find_one({"_id": oid(lost_ref), "type": "lost"}, PUBLIC) if lost_ref else None
    item_claims = list(claims.find({"report_id": doc["_id"]}).sort("created_at", -1)) if session.get("admin") else []
    return render_template("item.html", item=doc, matches=matches, lost_doc=lost_doc, item_claims=item_claims)


@app.route("/item/<item_id>/claim", methods=["POST"])
def claim(item_id):
    doc = reports.find_one({"_id": oid(item_id), "type": "found", "status": "open"}, PUBLIC)
    if not doc:
        abort(404)
    f = request.form
    lost_id = oid(f["lost_id"]) if f.get("lost_id") else None
    claim_doc = {
        "report_id": doc["_id"],
        "lost_report_id": lost_id,
        "claimant": {"name": f.get("name", "").strip(), "email": f.get("email", "").strip().lower()},
        "proof": f.get("proof", "").strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    try:
        claims.insert_one(claim_doc)
        flash("Claim sent. An admin will verify it and contact you.", "ok")
    except WriteError:
        flash("Please fill in your name, email and a proof description of at least 10 characters.", "error")
    return redirect(url_for("item", item_id=item_id))


@app.route("/image/<image_id>")
def image(image_id):
    try:
        f = fs.get(oid(image_id))
    except gridfs.errors.NoFile:
        abort(404)
    return Response(f.read(), mimetype=f.content_type or "image/jpeg")


@app.route("/search")
def search():
    a = request.args
    q = a.get("q", "").strip()
    mode = a.get("mode", "keyword")
    place = a.get("location", "")
    radius = int(a.get("radius") or 300)
    filt = {}
    if a.get("type") in ("lost", "found"):
        filt["type"] = a["type"]
    if a.get("category") in config.CATEGORIES:
        filt["category"] = a["category"]
    if not a.get("include_resolved"):
        filt["status"] = "open"
    try:
        when = {}
        if a.get("date_from"):
            when["$gte"] = datetime.strptime(a["date_from"], "%Y-%m-%d").replace(tzinfo=IST)
        if a.get("date_to"):
            when["$lte"] = datetime.strptime(a["date_to"] + " 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        if when:
            filt["event_time"] = when
    except ValueError:
        flash("Dates should look like 2026-10-02.", "error")
    center = {"type": "Point", "coordinates": list(config.LOCATIONS[place])} if place in config.LOCATIONS else None
    results, searched = [], bool(a)
    if q and mode == "semantic":
        # meaning-based: "earphones" also finds "AirPods"
        hits = vector_search(embed_query(q), filt, limit=30)
        for d, cos in hits:
            d["distance"] = distance_m(center, d["location"]["geo"]) if center else None
            if cos >= config.TEXT_SIM_LOW and (not center or d["distance"] <= radius):
                d["relevance"] = round(cos, 2)
                results.append(d)
    elif q:
        # keyword: $text index, optionally limited to a circle around a place
        if center:
            filt["location.geo"] = {"$geoWithin": {"$centerSphere": [center["coordinates"], radius / 6378100]}}
        filt["$text"] = {"$search": q}
        proj = dict(PUBLIC, relevance={"$meta": "textScore"})
        results = list(reports.find(filt, proj).sort([("relevance", {"$meta": "textScore"})]).limit(30))
        for d in results:
            d["distance"] = distance_m(center, d["location"]["geo"]) if center else None
            d["relevance"] = round(d["relevance"], 2)
    elif center:
        # no text, just "what's near this place", nearest first
        results = list(reports.aggregate([
            {"$geoNear": {"near": center, "distanceField": "distance", "maxDistance": radius, "query": filt, "spherical": True}},
            {"$project": PUBLIC},
            {"$limit": 30},
        ]))
    elif searched:
        results = list(reports.find(filt, PUBLIC).sort("event_time", -1).limit(30))
    return render_template("search.html", results=results, args=a, searched=searched)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == config.ADMIN_PASSWORD:
            session["admin"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("admin"))
        flash("Wrong password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", s=dashboard_stats(), pending=pending_claims(), center=config.CAMPUS_CENTER)


@app.route("/admin/claims/<claim_id>/<action>", methods=["POST"])
@admin_required
def review_claim(claim_id, action):
    c = claims.find_one({"_id": oid(claim_id), "status": "pending"})
    if not c or action not in ("approve", "reject"):
        abort(404)
    now = datetime.now(timezone.utc)
    if action == "reject":
        claims.update_one({"_id": c["_id"]}, {"$set": {"status": "rejected", "reviewed_at": now}})
        flash("Claim rejected.", "ok")
        return redirect(url_for("admin"))
    # approve: close the found report (and the linked lost report), reject other claims on it
    claims.update_one({"_id": c["_id"]}, {"$set": {"status": "approved", "reviewed_at": now}})
    claims.update_many({"report_id": c["report_id"], "status": "pending"}, {"$set": {"status": "rejected", "reviewed_at": now}})
    ids = [c["report_id"]] + ([c["lost_report_id"]] if c.get("lost_report_id") else [])
    reports.update_many({"_id": {"$in": ids}}, {"$set": {"status": "resolved", "resolved_at": now}})
    flash("Claim approved, the item is marked as returned.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/item/<item_id>/resolve", methods=["POST"])
@admin_required
def resolve_item(item_id):
    reports.update_one({"_id": oid(item_id)}, {"$set": {"status": "resolved", "resolved_at": datetime.now(timezone.utc)}})
    flash("Report marked as resolved.", "ok")
    return redirect(url_for("item", item_id=item_id))


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
