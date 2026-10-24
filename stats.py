import os, sys
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv(override=True)

match_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
balls = MongoClient(uri)["pitchside_live"]["balls"]

pipeline = [
    {"$match": {"match_id": match_id, "extra_type": None}},
    {"$group": {"_id": "$batter_name",
                "runs": {"$sum": "$runs"},
                "balls": {"$sum": 1},
                "fours": {"$sum": {"$cond": [{"$eq": ["$runs", 4]}, 1, 0]}},
                "sixes": {"$sum": {"$cond": [{"$eq": ["$runs", 6]}, 1, 0]}}}},
    {"$addFields": {"strike_rate": {"$round":
        [{"$multiply": [{"$divide": ["$runs", "$balls"]}, 100]}, 1]}}},
    {"$sort": {"runs": -1}},
    {"$limit": 5},
]
print("TOP 5 BATTERS")
for r in balls.aggregate(pipeline):
    print(r["_id"], "| runs", r["runs"], "| balls", r["balls"],
          "| 4s", r["fours"], "| 6s", r["sixes"], "| SR", r["strike_rate"])