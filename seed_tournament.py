"""Creates a realistic demo tournament: 6 teams, 66 players, 10 finished matches ball by ball, 1 live match, 4 upcoming fixtures."""
import os, random, itertools, datetime
import mysql.connector, redis
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
load_dotenv(override=True)
random.seed(11)
db = mysql.connector.connect(host="localhost", user="root", password=os.getenv("MYSQL_PASSWORD"), database="pitchside", autocommit=True)
cur = db.cursor(dictionary=True)
R = redis.Redis(host=os.getenv("REDIS_HOST"), port=int(os.getenv("REDIS_PORT")), password=os.getenv("REDIS_PASSWORD"), decode_responses=True)
balls = MongoClient(os.getenv("MONGO_URI") or os.getenv("MONGODB_URI"))["pitchside_live"]["balls"]
if input("This RESETS teams, players, matches and balls. Type YES to continue: ").strip() != "YES": raise SystemExit("Cancelled")

TEAMS = [("MUJ Jaguars", "MUJ", "Manipal University Jaipur"), ("MNIT Titans", "MNT", "MNIT Jaipur"), ("JECRC Royals", "JEC", "JECRC University"),
         ("LNMIIT Lions", "LNM", "LNMIIT Jaipur"), ("Poornima Panthers", "POR", "Poornima University"), ("Arya Strikers", "ARY", "Arya College")]
FIRST = "Aarav Vihaan Arjun Rohan Kabir Ishaan Dev Yash Neel Krish Parth Dhruv Karan Om Veer Manav Aditya Reyansh Samar Tanay Harsh Rahul Naman Pranav Shaurya Lakshya Ayaan Rudra Kunal Siddharth".split()
LAST = "Mehta Singh Jain Sharma Gupta Nair Shah Kapoor Rao Verma Joshi Das Khan Iyer Bose Malhotra Sethi Pandey Chauhan Reddy Agarwal Bansal Saxena Rathore Shekhawat".split()
ROLES = ["Batter"] * 4 + ["Wicketkeeper"] + ["All-rounder"] * 2 + ["Bowler"] * 4
OUTS, OVERS = ["bowled", "caught", "lbw", "run out"], 8

cur.execute("SET FOREIGN_KEY_CHECKS=0")
for t in ("matches", "players", "points_table", "teams", "match_log"): cur.execute(f"TRUNCATE TABLE {t}")
cur.execute("SET FOREIGN_KEY_CHECKS=1")
balls.delete_many({})
for k in list(R.scan_iter("match:*")) + list(R.scan_iter("cache:*")): R.delete(k)
cur.execute("SELECT venue_id FROM venues LIMIT 1"); v = cur.fetchone()
if not v:
    cur.execute("INSERT INTO venues (venue_name, city) VALUES ('MUJ Main Ground','Jaipur')"); venue = cur.lastrowid
else: venue = v["venue_id"]

used, squads = set(), {}
for name, code, college in TEAMS:
    cur.execute("INSERT INTO teams (team_name, short_code, college) VALUES (%s,%s,%s)", (name, code, college)); tid = cur.lastrowid
    cur.execute("INSERT INTO points_table (team_id) VALUES (%s)", (tid,))
    squad = []
    for role, j in zip(ROLES, random.sample(range(1, 100), 11)):
        while True:
            n = f"{random.choice(FIRST)} {random.choice(LAST)}"
            if n not in used: used.add(n); break
        cur.execute("INSERT INTO players (team_id, full_name, role, jersey_no) VALUES (%s,%s,%s,%s)", (tid, n, role, j))
        squad.append({"id": cur.lastrowid, "name": n, "role": role})
    squads[tid] = squad
tids = list(squads)

def innings(mid, inn, bat_t, bowl_t, start, target=None, max_balls=None):
    bat = squads[bat_t]; bowl = [p for p in squads[bowl_t] if p["role"] in ("Bowler", "All-rounder")]
    cr, nxt, st, runs, wk, legal, docs, clock, over = [0, 1], 2, 0, 0, 0, 0, [], start, 0
    done = lambda: wk >= 10 or (target and runs >= target) or (max_balls and legal >= max_balls)
    while over < OVERS and not done():
        bw, lb = bowl[over % len(bowl)], 0
        while lb < 6 and not done():
            b = bat[cr[st]]; clock += datetime.timedelta(seconds=25)
            d = {"match_id": mid, "innings": inn, "over": over + 1, "ball": lb + 1, "batting_team_id": bat_t, "batter_id": b["id"], "batter_name": b["name"],
                 "bowler_id": bw["id"], "bowler_name": bw["name"], "runs": 0, "extra_type": None, "is_wicket": False, "wicket_type": None, "ts": clock}
            x = random.random()
            if x < 0.04: d.update(runs=1, extra_type="wide", commentary="Wide ball"); runs += 1
            else:
                lb += 1; legal += 1
                if x < 0.10:
                    wk += 1; w = random.choice(OUTS); d.update(is_wicket=True, wicket_type=w, commentary=f"OUT! {b['name']} {w}")
                    if nxt < len(bat): cr[st] = nxt; nxt += 1
                else:
                    r = random.choices([0, 1, 2, 3, 4, 6], [28, 32, 10, 1, 15, 9])[0]; runs += r
                    d.update(runs=r, commentary="SIX!" if r == 6 else "FOUR!" if r == 4 else f"{r} run(s)")
                    if r % 2: st ^= 1
            docs.append(d)
        st ^= 1; over += 1
    return runs, wk, legal, docs

now = datetime.datetime.now(); pairs = list(itertools.combinations(tids, 2)); random.shuffle(pairs)
for i, (a, b) in enumerate(pairs):
    if i <= 10:
        tw, dec = random.choice([a, b]), random.choice(["Bat", "Bowl"]); other = b if tw == a else a
        f, s = (tw, other) if dec == "Bat" else (other, tw)
        day = (now - datetime.timedelta(days=11 - i)).date()
        cur.execute("INSERT INTO matches (team1_id,team2_id,venue_id,match_date,overs,stage,status,toss_winner_id,toss_decision) VALUES (%s,%s,%s,%s,%s,'League','Live',%s,%s)", (f, s, venue, day, OVERS, tw, dec))
        mid = cur.lastrowid; t0 = now - datetime.timedelta(days=11 - i)
        r1, w1, l1, d1 = innings(mid, 1, f, s, t0)
        if i < 10:
            r2, w2, l2, d2 = innings(mid, 2, s, f, t0 + datetime.timedelta(hours=1), target=r1 + 1)
            balls.insert_many(d1 + d2)
            win = s if r2 > r1 else f
            cur.execute("CALL complete_match(%s,%s)", (mid, win))
            if r1 == r2: cur.execute("UPDATE matches SET result_note='Tie, won in super over' WHERE match_id=%s", (mid,))
        else:
            _, _, _, d2 = innings(mid, 2, s, f, now - datetime.timedelta(minutes=12), target=r1 + 1, max_balls=22)
            balls.insert_many(d1 + d2)
    else:
        cur.execute("INSERT INTO matches (team1_id,team2_id,venue_id,match_date,overs,stage,status) VALUES (%s,%s,%s,%s,%s,'League','Scheduled')",
                    (a, b, venue, (now + datetime.timedelta(days=i - 10)).date(), OVERS))
balls.create_index([("match_id", ASCENDING), ("innings", ASCENDING), ("over", ASCENDING), ("ball", ASCENDING)])
p = R.pipeline()
for d in balls.find().sort("ts", 1):
    key = f"match:{d['match_id']}:inn{d['innings']}"
    p.hincrby(key, "runs", d["runs"])
    if d["extra_type"] is None: p.hincrby(key, "balls", 1); p.zincrby(f"match:{d['match_id']}:top", d["runs"], d["batter_name"])
    if d["is_wicket"]: p.hincrby(key, "wickets", 1)
    tag = "W" if d["is_wicket"] else ("Wd" if d["extra_type"] else str(d["runs"]))
    p.lpush(f"match:{d['match_id']}:recent", f"{tag}|{d['over']}.{d['ball']}|{d.get('commentary', '')}"); p.ltrim(f"match:{d['match_id']}:recent", 0, 5)
p.execute()
print("Done:", len(TEAMS), "teams,", len(used), "players, 10 results, 1 live match, 4 fixtures,", balls.count_documents({}), "balls. Open http://localhost:8000")
