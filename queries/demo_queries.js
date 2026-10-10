// Queries to run live in mongosh (or the Atlas "Shell" tab) during the demo.
// Connect with: mongosh "<your MONGO_URI>/lost_found"   then:  load("queries/demo_queries.js")
// Or copy one block at a time into the shell.

// 1. Flexible schema: two reports in the same collection with different attribute fields
printjson(db.reports.find({}, {_id: 0, category: 1, title: 1, attributes: 1}).limit(4).toArray());

// 2. Schema validation: this insert is rejected because "type" must be lost/found and fields are missing
try {
  db.reports.insertOne({type: "stolen", title: "x"});
} catch (e) {
  print("rejected by validator: " + e.errInfo?.details?.operatorName);
}

// 3. CRUD + embedded documents: query inside the embedded location and attributes objects
printjson(db.reports.find(
  {"location.name": "Central Library", "attributes.device_type": "Earbuds"},
  {_id: 0, type: 1, title: 1, "location.name": 1}
).toArray());

// 4. Text index search, sorted by relevance
printjson(db.reports.find(
  {$text: {$search: "wallet leather"}},
  {_id: 0, title: 1, score: {$meta: "textScore"}}
).sort({score: {$meta: "textScore"}}).toArray());

// 5. Geospatial: everything within 150 m of the Central Library, nearest first
printjson(db.reports.aggregate([
  {$geoNear: {near: {type: "Point", coordinates: [75.5652, 26.8437]}, distanceField: "metres", maxDistance: 150, spherical: true}},
  {$project: {_id: 0, title: 1, "location.name": 1, metres: {$round: ["$metres", 0]}}}
]).toArray());

// 6. Indexing: the compound index is used (look for IXSCAN, not COLLSCAN)
const plan = db.reports.find({type: "found", status: "open"}).sort({event_time: -1}).explain("executionStats");
function stages(p) { const s = []; while (p) { if (p.stage) s.push(p.stage); p = p.inputStage || p.queryPlan; } return s; }
print("plan: " + stages(plan.queryPlanner.winningPlan).join(" <- "));
print("docs examined: " + plan.executionStats.totalDocsExamined);

// 7. Aggregation: lost vs found per category
printjson(db.reports.aggregate([
  {$group: {_id: "$category",
    lost: {$sum: {$cond: [{$eq: ["$type", "lost"]}, 1, 0]}},
    found: {$sum: {$cond: [{$eq: ["$type", "found"]}, 1, 0]}}}},
  {$sort: {lost: -1}}
]).toArray());

// 8. Aggregation over a flexible schema: which attribute keys exist and how often
printjson(db.reports.aggregate([
  {$project: {kv: {$objectToArray: "$attributes"}}},
  {$unwind: "$kv"},
  {$group: {_id: "$kv.k", n: {$sum: 1}}},
  {$sort: {n: -1}}
]).toArray());

// 9. $lookup: pending claims joined with the report they refer to
printjson(db.claims.aggregate([
  {$match: {status: "pending"}},
  {$lookup: {from: "reports", localField: "report_id", foreignField: "_id", as: "item"}},
  {$unwind: "$item"},
  {$project: {_id: 0, "claimant.name": 1, proof: 1, "item.title": 1}}
]).toArray());

// 10. Vector search: found items closest in meaning to the lost AirPods report
const airpods = db.reports.findOne({title: "Black AirPods Pro"});
printjson(db.reports.aggregate([
  {$vectorSearch: {index: "report_vectors", path: "embedding", queryVector: airpods.embedding,
    numCandidates: 100, limit: 5, filter: {type: {$eq: "found"}}}},
  {$project: {_id: 0, title: 1, "location.name": 1, score: {$meta: "vectorSearchScore"}}}
]).toArray());
