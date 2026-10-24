import os, sys, random
from datetime import datetime, timedelta
import mysql.connector
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
load_dotenv(override=True)

match_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1

db = mysql.connector.connect(host="localhost", user="root",
     password=os.getenv("MYSQL_PASSWORD"), database="pitchside")
cur = db.cursor(dictionary=True)
cur.execute("SELECT team1_id, team2_id, overs FROM matches WHERE match_id=%s", (match_id,))
m = cur.fetchone()
if not m:
    sys.exit("Match not found in MySQL")

def squad(team_id):
    cur.execute("SELECT player_id, full_name, role FROM players "
                "WHERE team_id=%s ORDER BY jersey_no", (team_id,))
    return cur.fetchall()

uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
balls = MongoClient(uri)["pitchside_live"]["balls"]
balls.delete_many({"match_id": match_id})      # safe to re-run

RUNS, WEIGHTS = [0, 1, 2, 3, 4, 6], [30, 30, 10, 2, 14, 8]
OUTS = ["bowled", "caught", "lbw", "run out"]
docs, clock = [], datetime.now()

for innings, (bat_id, bowl_id) in enumerate(
        [(m["team1_id"], m["team2_id"]), (m["team2_id"], m["team1_id"])], start=1):
    batters = squad(bat_id)
    bowlers = [p for p in squad(bowl_id) if p["role"] in ("Bowler", "All-rounder")]
    crease, next_in, strike, all_out = [0, 1], 2, 0, False
    for over in range(m["overs"]):
        bowler, legal = bowlers[over % len(bowlers)], 0
        while legal < 6 and not all_out:
            b = batters[crease[strike]]
            clock += timedelta(seconds=20)
            doc = {"match_id": match_id, "innings": innings, "over": over + 1,
                   "ball": legal + 1, "batting_team_id": bat_id,
                   "batter_id": b["player_id"], "batter_name": b["full_name"],
                   "bowler_id": bowler["player_id"], "bowler_name": bowler["full_name"],
                   "runs": 0, "extra_type": None, "is_wicket": False,
                   "wicket_type": None, "ts": clock}
            if random.random() < 0.04:                      # a wide
                doc.update(runs=1, extra_type="wide", commentary="Wide ball")
            else:
                legal += 1
                if random.random() < 0.06:                  # a wicket
                    doc.update(is_wicket=True, wicket_type=random.choice(OUTS))
                    doc["commentary"] = f"OUT! {b['full_name']} {doc['wicket_type']}"
                    if next_in >= len(batters):
                        all_out = True
                    else:
                        crease[strike] = next_in
                        next_in += 1
                else:
                    r = random.choices(RUNS, WEIGHTS)[0]
                    doc["runs"] = r
                    doc["commentary"] = ("SIX!" if r == 6 else "FOUR!" if r == 4
                                         else f"{r} run(s)")
                    if r % 2 == 1:
                        strike ^= 1
            docs.append(doc)
        strike ^= 1
        if all_out:
            break

balls.insert_many(docs)
balls.create_index([("match_id", ASCENDING), ("innings", ASCENDING),
                    ("over", ASCENDING), ("ball", ASCENDING)])
print("Inserted", len(docs), "ball documents into MongoDB for match", match_id)