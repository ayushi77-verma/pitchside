import os, json, time, datetime
import bcrypt, jwt, redis, redis.asyncio as aredis, mysql.connector
from mysql.connector import pooling
from pymongo import MongoClient
from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
import csv, io
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv(override=True)

SECRET = os.getenv("JWT_SECRET", "pitchside-change-this-secret")
pool = pooling.MySQLConnectionPool(pool_name="ps", pool_size=6, host="localhost", user="root",
                                   password=os.getenv("MYSQL_PASSWORD"), database="pitchside")
RK = dict(host=os.getenv("REDIS_HOST"), port=int(os.getenv("REDIS_PORT")),
          password=os.getenv("REDIS_PASSWORD"), decode_responses=True)
R, RA = redis.Redis(**RK), aredis.Redis(**RK)
DB = MongoClient(os.getenv("MONGO_URI") or os.getenv("MONGODB_URI"))["pitchside_live"]
balls, audit = DB["balls"], DB["audit"]
app = FastAPI(title="PitchSide API", version="2.0")

@app.exception_handler(mysql.connector.Error)
async def sql_err(request, e):
    return JSONResponse({"detail": e.msg}, status_code=400)

def q(sql, args=(), write=False):
    c = pool.get_connection()
    try:
        cur = c.cursor(dictionary=True); cur.execute(sql, args)
        if write:
            c.commit(); return cur.lastrowid
        return cur.fetchall()
    finally:
        c.close()

# ---------- users, JWT, roles, rate limit, audit ----------
q("CREATE TABLE IF NOT EXISTS users(user_id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(40) NOT NULL UNIQUE,"
  " password_hash VARCHAR(100) NOT NULL, role ENUM('admin','scorer') NOT NULL)", write=True)
if not q("SELECT 1 FROM users LIMIT 1"):
    for u, p, r in [("admin", "admin@123", "admin"), ("scorer", "scorer@123", "scorer")]:
        q("INSERT INTO users(username,password_hash,role) VALUES(%s,%s,%s)",
          (u, bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode(), r), write=True)

def log(user, action, data):
    audit.insert_one({"user": user, "action": action, "data": data, "ts": datetime.datetime.now()})

def need(*roles):
    def dep(request: Request):
        try:
            p = jwt.decode(request.headers.get("authorization", "")[7:], SECRET, algorithms=["HS256"])
        except Exception:
            raise HTTPException(401, "Please log in")
        if p["role"] not in roles:
            raise HTTPException(403, "Your role cannot do this")
        k = f"rl:{request.client.host}:{int(time.time() // 60)}"
        if R.incr(k) > 300:
            raise HTTPException(429, "Too many requests")
        R.expire(k, 70)
        return p
    return dep

class Login(BaseModel):
    username: str; password: str

@app.post("/api/login")
def login(b: Login):
    row = q("SELECT * FROM users WHERE username=%s", (b.username,))
    if not row or not bcrypt.checkpw(b.password.encode(), row[0]["password_hash"].encode()):
        raise HTTPException(401, "Wrong username or password")
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    tok = jwt.encode({"sub": b.username, "role": row[0]["role"], "exp": exp}, SECRET, "HS256")
    log(b.username, "login", {})
    return {"token": tok, "role": row[0]["role"], "user": b.username}

# ---------- Redis live state ----------
def apply_ball(p, d):
    mid, key = d["match_id"], f"match:{d['match_id']}:inn{d['innings']}"
    p.hincrby(key, "runs", d["runs"])
    if d["extra_type"] is None:
        p.hincrby(key, "balls", 1); p.zincrby(f"match:{mid}:top", d["runs"], d["batter_name"])
    if d["is_wicket"]:
        p.hincrby(key, "wickets", 1)
    tag = "W" if d["is_wicket"] else ("Wd" if d["extra_type"] else str(d["runs"]))
    p.lpush(f"match:{mid}:recent", f"{tag}|{d['over']}.{d['ball']}|{d.get('commentary', '')}")
    p.ltrim(f"match:{mid}:recent", 0, 5)

def snapshot(mid):
    m = q("""SELECT m.match_id,m.overs,m.status,t1.team_name team1,t2.team_name team2 FROM matches m
             JOIN teams t1 ON t1.team_id=m.team1_id JOIN teams t2 ON t2.team_id=m.team2_id WHERE m.match_id=%s""", (mid,))
    if not m: raise HTTPException(404, "Match not found")
    p = R.pipeline()
    p.hgetall(f"match:{mid}:inn1"); p.hgetall(f"match:{mid}:inn2")
    p.zrevrange(f"match:{mid}:top", 0, 4, withscores=True); p.lrange(f"match:{mid}:recent", 0, 5)
    s1, s2, top, rec = p.execute()
    g = lambda s: {k: int(s.get(k, 0)) for k in ("runs", "wickets", "balls")}
    return {"match": m[0], "inn1": g(s1), "inn2": g(s2),
            "top": [{"name": n, "runs": int(s)} for n, s in top], "recent": [x.split("|", 2) for x in rec]}

@app.websocket("/ws/live/{mid}")
async def live_ws(w: WebSocket, mid: int):
    await w.accept()
    await w.send_text(json.dumps(snapshot(mid), default=str))
    ps = RA.pubsub(); await ps.subscribe(f"live:{mid}")
    try:
        async for msg in ps.listen():
            if msg["type"] == "message":
                await w.send_text(msg["data"])
    except Exception:
        pass
    finally:
        await ps.aclose()

# ---------- read APIs ----------
@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/api/teams")
def teams():
    return q("SELECT team_id, team_name, short_code, college FROM teams ORDER BY team_name")

@app.get("/api/matches")
def matches():
    return q("""SELECT m.match_id,m.team1_id,m.team2_id,m.overs,m.status,m.match_date,m.winner_team_id,m.stage,m.toss_winner_id,m.toss_decision,m.result_note,
                t1.team_name team1,t2.team_name team2 FROM matches m JOIN teams t1 ON t1.team_id=m.team1_id
                JOIN teams t2 ON t2.team_id=m.team2_id ORDER BY m.match_id DESC""")

@app.get("/api/matches/{mid}/squads")
def squads(mid: int):
    m = q("SELECT team1_id, team2_id FROM matches WHERE match_id=%s", (mid,))
    if not m: raise HTTPException(404, "Match not found")
    f = lambda t: q("SELECT player_id id, full_name name FROM players WHERE team_id=%s ORDER BY jersey_no", (t,))
    return {"team1": f(m[0]["team1_id"]), "team2": f(m[0]["team2_id"])}

@app.get("/api/dashboard")
def dashboard():
    c = R.get("cache:dash")
    if c: return json.loads(c)
    orange = balls.aggregate([{"$match": {"extra_type": None}}, {"$group": {"_id": "$batter_name", "runs": {"$sum": "$runs"}}},
                              {"$sort": {"runs": -1}}, {"$limit": 6}])
    d = {"kpi": {"matches": q("SELECT COUNT(*) n FROM matches")[0]["n"], "players": q("SELECT COUNT(*) n FROM players")[0]["n"],
                 "balls": balls.count_documents({}), "sixes": balls.count_documents({"runs": 6, "extra_type": None})},
         "standings": q("SELECT * FROM v_points_standings"),
         "orange": [{"name": x["_id"], "runs": x["runs"]} for x in orange], "recent": matches()[:6]}
    d["live"] = [snapshot(x["match_id"]) for x in d["recent"] if x["status"] == "Live"]
    mix = {}
    for x in balls.aggregate([{"$group": {"_id": {"r": "$runs", "w": "$is_wicket", "x": "$extra_type"}, "n": {"$sum": 1}}}]):
        i = x["_id"]; k = "Wicket" if i["w"] else "Wide" if i["x"] else ("Dot" if i["r"] == 0 else str(i["r"]))
        mix[k] = mix.get(k, 0) + x["n"]
    d["mix"] = mix
    R.setex("cache:dash", 10, json.dumps(d, default=str))
    return d

@app.get("/api/matches/{mid}/snapshot")
def snap(mid: int):
    return snapshot(mid)

@app.get("/api/matches/{mid}/analytics")
def analytics(mid: int):
    mm = q("""SELECT m.*,t1.team_name team1,t2.team_name team2,tw.team_name toss FROM matches m JOIN teams t1 ON t1.team_id=m.team1_id
              JOIN teams t2 ON t2.team_id=m.team2_id LEFT JOIN teams tw ON tw.team_id=m.toss_winner_id WHERE m.match_id=%s""", (mid,))
    if not mm: raise HTTPException(404, "Match not found")
    mm = mm[0]
    per = [{"inn": x["_id"]["i"], "over": x["_id"]["o"], "runs": x["runs"]} for x in balls.aggregate([{"$match": {"match_id": mid}},
           {"$group": {"_id": {"i": "$innings", "o": "$over"}, "runs": {"$sum": "$runs"}}}, {"$sort": {"_id.i": 1, "_id.o": 1}}])]
    bat = [{**x, "name": x["_id"], "sr": round(x["runs"] * 100 / x["balls"], 1)} for x in balls.aggregate([
        {"$match": {"match_id": mid, "extra_type": None}},
        {"$group": {"_id": "$batter_name", "runs": {"$sum": "$runs"}, "balls": {"$sum": 1},
                    "fours": {"$sum": {"$cond": [{"$eq": ["$runs", 4]}, 1, 0]}}, "sixes": {"$sum": {"$cond": [{"$eq": ["$runs", 6]}, 1, 0]}}}},
        {"$sort": {"runs": -1}}])]
    bow = [{**x, "name": x["_id"], "eco": round(x["runs"] * 6 / x["balls"], 2) if x["balls"] else 0} for x in balls.aggregate([
        {"$match": {"match_id": mid}}, {"$group": {"_id": "$bowler_name", "runs": {"$sum": "$runs"},
         "balls": {"$sum": {"$cond": [{"$eq": ["$extra_type", None]}, 1, 0]}}, "wickets": {"$sum": {"$cond": ["$is_wicket", 1, 0]}}}},
        {"$sort": {"wickets": -1}}])]
    wk = [{"type": x["_id"], "n": x["n"]} for x in balls.aggregate([{"$match": {"match_id": mid, "is_wicket": True}}, {"$group": {"_id": "$wicket_type", "n": {"$sum": 1}}}])]
    pts = {}
    for x in bat: pts[x["name"]] = pts.get(x["name"], 0) + x["runs"]
    for x in bow: pts[x["name"]] = pts.get(x["name"], 0) + 25 * x["wickets"]
    potm = max(pts.items(), key=lambda kv: kv[1]) if pts else None
    tot = {i: sum(x["runs"] for x in per if x["inn"] == i) for i in (1, 2)}
    res = "In progress"
    if mm["status"] == "Scheduled": res = "Yet to start"
    elif mm["status"] == "Abandoned": res = "Match abandoned" + (f" ({mm['result_note']})" if mm["result_note"] else "")
    elif mm["status"] == "Tied": res = "Match tied. 1 point each"
    elif mm["status"] == "Completed" and mm["result_note"] and "super over" in mm["result_note"]:
        res = f"Scores tied. {mm['team1'] if mm['winner_team_id'] == mm['team1_id'] else mm['team2']} won the super over"
    elif mm["status"] == "Completed":
        if mm["winner_team_id"] == mm["team1_id"]: res = f"{mm['team1']} won by {tot[1] - tot[2]} runs"
        else:
            sq = q("SELECT COUNT(*) n FROM players WHERE team_id=%s", (mm["team2_id"],))[0]["n"]
            w2 = balls.count_documents({"match_id": mid, "innings": 2, "is_wicket": True})
            res = f"{mm['team2']} won by {max(sq - 1 - w2, 0)} wickets"
    toss = f"Toss: {mm['toss']} chose to {mm['toss_decision'].lower()}" if mm["toss"] else ""
    return {"per_over": per, "batters": bat, "bowlers": bow, "wickets": wk,
            "summary": {"title": f"{mm['team1']} vs {mm['team2']}", "result": res, "totals": tot, "toss": toss,
                        "potm": {"name": potm[0], "score": potm[1]} if potm else None}}

@app.get("/api/players")
def players():
    P = q("SELECT p.player_id,p.team_id,p.full_name,p.role,p.jersey_no,t.team_name FROM players p JOIN teams t USING(team_id) ORDER BY t.team_name,p.jersey_no")
    bat = {x["_id"]: x for x in balls.aggregate([{"$match": {"extra_type": None}}, {"$group": {"_id": "$batter_id", "runs": {"$sum": "$runs"},
           "balls": {"$sum": 1}, "sixes": {"$sum": {"$cond": [{"$eq": ["$runs", 6]}, 1, 0]}}}}])}
    bow = {x["_id"]: x["w"] for x in balls.aggregate([{"$match": {"is_wicket": True}}, {"$group": {"_id": "$bowler_id", "w": {"$sum": 1}}}])}
    for p in P:
        b = bat.get(p["player_id"], {})
        p.update(runs=b.get("runs", 0), balls=b.get("balls", 0), sixes=b.get("sixes", 0), wickets=bow.get(p["player_id"], 0),
                 sr=round(b["runs"] * 100 / b["balls"], 1) if b.get("balls") else 0)
    return P

def ms(f):
    t = time.perf_counter(); f(); return round((time.perf_counter() - t) * 1000, 1)

@app.get("/api/health")
def health():
    return {"mysql": ms(lambda: q("SELECT 1")), "mongo": ms(lambda: DB.command("ping")), "redis": ms(R.ping),
            "redis_keys": R.dbsize(), "balls": balls.count_documents({}),
            "explain": q("EXPLAIN SELECT * FROM matches WHERE match_date=CURDATE() AND status='Live'")}

@app.get("/api/audit")
def audit_log(u=Depends(need("admin"))):
    return list(audit.find({}, {"_id": 0}).sort("ts", -1).limit(15))

# ---------- write APIs (role protected) ----------
class NewMatch(BaseModel):
    team1: int; team2: int; overs: int = 5; date: str | None = None; stage: str = "League"

@app.post("/api/matches")
def new_match(b: NewMatch, u=Depends(need("admin"))):
    v = q("SELECT venue_id FROM venues LIMIT 1")[0]["venue_id"]
    i = q("INSERT INTO matches (team1_id,team2_id,venue_id,match_date,overs,stage,status) VALUES (%s,%s,%s,COALESCE(%s,CURDATE()),%s,%s,'Scheduled')",
          (b.team1, b.team2, v, b.date or None, b.overs, b.stage), write=True)
    R.delete("cache:dash"); log(u["sub"], "create_match", {"match": i})
    return {"match_id": i}

@app.post("/api/matches/{mid}/complete/{winner}")
def complete(mid: int, winner: int, u=Depends(need("admin"))):
    q("CALL complete_match(%s,%s)", (mid, winner), write=True)
    R.delete("cache:dash"); log(u["sub"], "complete_match", {"match": mid, "winner": winner})
    R.publish(f"live:{mid}", json.dumps(snapshot(mid), default=str))
    return {"ok": True}

@app.post("/api/matches/{mid}/rebuild")
def rebuild(mid: int, u=Depends(need("admin"))):
    keys = list(R.scan_iter(f"match:{mid}:*")); p = R.pipeline()
    if keys: p.delete(*keys)
    for d in balls.find({"match_id": mid}).sort([("innings", 1), ("over", 1), ("ball", 1)]): apply_ball(p, d)
    p.execute(); return {"ok": True}

class Ball(BaseModel):
    innings: int; striker: int; bowler: int; kind: str; wicket_type: str = "bowled"

@app.post("/api/matches/{mid}/ball")
def score(mid: int, b: Ball, u=Depends(need("admin", "scorer"))):
    m = q("SELECT * FROM matches WHERE match_id=%s AND status='Live'", (mid,))
    if not m: raise HTTPException(400, "Match is not live")
    m, inn = m[0], b.innings
    bat_t, bowl_t = (m["team1_id"], m["team2_id"]) if inn == 1 else (m["team2_id"], m["team1_id"])
    pm = {p["player_id"]: p for p in q("SELECT player_id, full_name, team_id FROM players WHERE player_id IN (%s,%s)", (b.striker, b.bowler))}
    if pm.get(b.striker, {}).get("team_id") != bat_t or pm.get(b.bowler, {}).get("team_id") != bowl_t:
        raise HTTPException(400, "Wrong striker or bowler for this innings")
    legal = balls.count_documents({"match_id": mid, "innings": inn, "extra_type": None})
    wk = balls.count_documents({"match_id": mid, "innings": inn, "is_wicket": True})
    squad = q("SELECT COUNT(*) n FROM players WHERE team_id=%s", (bat_t,))[0]["n"]
    if legal >= m["overs"] * 6 or wk >= squad - 1: raise HTTPException(400, "This innings is finished")
    name = pm[b.striker]["full_name"]
    d = {"match_id": mid, "innings": inn, "over": legal // 6 + 1, "ball": legal % 6 + 1, "batting_team_id": bat_t,
         "batter_id": b.striker, "batter_name": name, "bowler_id": b.bowler, "bowler_name": pm[b.bowler]["full_name"],
         "runs": 0, "extra_type": None, "is_wicket": False, "wicket_type": None, "ts": datetime.datetime.now()}
    k = b.kind
    if k == "Wd": d.update(runs=1, extra_type="wide", commentary="Wide ball")
    elif k == "W": d.update(is_wicket=True, wicket_type=b.wicket_type, commentary=f"OUT! {name} {b.wicket_type}")
    elif k in ("0", "1", "2", "3", "4", "6"):
        r = int(k); d.update(runs=r, commentary="SIX!" if r == 6 else "FOUR!" if r == 4 else f"{r} run(s)")
    else: raise HTTPException(400, "Bad ball type")
    balls.insert_one(d)                                   # permanent record
    p = R.pipeline(); apply_ball(p, d); p.execute()       # live counters
    R.publish(f"live:{mid}", json.dumps(snapshot(mid), default=str))   # push to every open scoreboard
    R.delete("cache:dash"); log(u["sub"], "ball", {"match": mid, "kind": k})
    return {"ok": True, "commentary": d["commentary"]}


# ================= v3: real tournament features =================
def ensure_col(t, c, ddl):
    if not q("SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s", (t, c)):
        q(f"ALTER TABLE {t} ADD COLUMN {c} {ddl}", write=True)
ensure_col("matches", "stage", "VARCHAR(20) NOT NULL DEFAULT 'League'")
ensure_col("matches", "toss_winner_id", "INT NULL")
ensure_col("matches", "toss_decision", "VARCHAR(5) NULL")
ensure_col("matches", "result_note", "VARCHAR(120) NULL")
q("ALTER TABLE matches MODIFY status ENUM('Scheduled','Live','Completed','Abandoned','Tied') NOT NULL DEFAULT 'Scheduled'", write=True)

def recalc_points():
    """Rebuild the whole points table from match results (used after delete / reopen / abandon / tie)."""
    q("""UPDATE points_table p SET
         played=(SELECT COUNT(*) FROM matches m WHERE m.status IN ('Completed','Abandoned','Tied') AND p.team_id IN (m.team1_id,m.team2_id)),
         won=(SELECT COUNT(*) FROM matches m WHERE m.status='Completed' AND m.winner_team_id=p.team_id),
         lost=(SELECT COUNT(*) FROM matches m WHERE m.status='Completed' AND p.team_id IN (m.team1_id,m.team2_id) AND m.winner_team_id<>p.team_id),
         tied=(SELECT COUNT(*) FROM matches m WHERE m.status='Tied' AND p.team_id IN (m.team1_id,m.team2_id)),
         points=(SELECT COUNT(*) FROM matches m WHERE m.status='Completed' AND m.winner_team_id=p.team_id)*2
               +(SELECT COUNT(*) FROM matches m WHERE m.status IN ('Abandoned','Tied') AND p.team_id IN (m.team1_id,m.team2_id))""", write=True)

def rebuild_live(mid):
    keys = list(R.scan_iter(f"match:{mid}:*")); p = R.pipeline()
    if keys: p.delete(*keys)
    for d in balls.find({"match_id": mid}).sort([("innings", 1), ("over", 1), ("ball", 1)]): apply_ball(p, d)
    p.execute(); R.delete("cache:dash")
    R.publish(f"live:{mid}", json.dumps(snapshot(mid), default=str))

class StartM(BaseModel):
    toss_winner: int; decision: str

@app.post("/api/matches/{mid}/start")
def start(mid: int, b: StartM, u=Depends(need("admin", "scorer"))):
    m = q("SELECT * FROM matches WHERE match_id=%s AND status='Scheduled'", (mid,))
    if not m: raise HTTPException(400, "Only a scheduled match can be started")
    t1, t2 = m[0]["team1_id"], m[0]["team2_id"]
    if b.toss_winner not in (t1, t2) or b.decision not in ("Bat", "Bowl"): raise HTTPException(400, "Invalid toss")
    other = t2 if b.toss_winner == t1 else t1
    first, second = (b.toss_winner, other) if b.decision == "Bat" else (other, b.toss_winner)   # batting order follows the toss
    q("UPDATE matches SET status='Live', toss_winner_id=%s, toss_decision=%s, team1_id=%s, team2_id=%s WHERE match_id=%s",
      (b.toss_winner, b.decision, first, second, mid), write=True)
    R.delete("cache:dash"); log(u["sub"], "start_match", {"match": mid, "toss": b.toss_winner, "decision": b.decision})
    return {"ok": True}

class EditM(BaseModel):
    overs: int | None = None; stage: str | None = None; date: str | None = None; team1: int | None = None; team2: int | None = None

@app.put("/api/matches/{mid}")
def edit_match(mid: int, b: EditM, u=Depends(need("admin"))):
    if (b.team1 or b.team2) and balls.count_documents({"match_id": mid}):
        raise HTTPException(400, "Teams cannot change after scoring started. Delete the match and recreate it.")
    q("UPDATE matches SET overs=COALESCE(%s,overs), stage=COALESCE(%s,stage), match_date=COALESCE(%s,match_date), "
      "team1_id=COALESCE(%s,team1_id), team2_id=COALESCE(%s,team2_id) WHERE match_id=%s",
      (b.overs, b.stage, b.date or None, b.team1, b.team2, mid), write=True)
    R.delete("cache:dash"); log(u["sub"], "edit_match", {"match": mid, **b.model_dump(exclude_none=True)}); return {"ok": True}

@app.delete("/api/matches/{mid}")
def delete_match(mid: int, u=Depends(need("admin"))):
    q("DELETE FROM matches WHERE match_id=%s", (mid,), write=True)
    n = balls.delete_many({"match_id": mid}).deleted_count
    for k in list(R.scan_iter(f"match:{mid}:*")): R.delete(k)
    recalc_points(); R.delete("cache:dash"); log(u["sub"], "delete_match", {"match": mid, "balls_removed": n}); return {"ok": True}

class Abandon(BaseModel):
    reason: str = ""

@app.post("/api/matches/{mid}/abandon")
def abandon(mid: int, b: Abandon, u=Depends(need("admin"))):
    if not q("SELECT 1 FROM matches WHERE match_id=%s AND status IN ('Scheduled','Live')", (mid,)): raise HTTPException(400, "Only an unfinished match can be abandoned")
    q("UPDATE matches SET status='Abandoned', result_note=%s WHERE match_id=%s", (b.reason[:120], mid), write=True)
    recalc_points(); R.delete("cache:dash"); log(u["sub"], "abandon_match", {"match": mid, "reason": b.reason}); return {"ok": True}

@app.post("/api/matches/{mid}/reopen")
def reopen(mid: int, u=Depends(need("admin"))):
    if not q("SELECT 1 FROM matches WHERE match_id=%s AND status IN ('Completed','Abandoned','Tied')", (mid,)): raise HTTPException(400, "Only a finished match can be reopened")
    q("UPDATE matches SET status='Live', winner_team_id=NULL, result_note=NULL WHERE match_id=%s", (mid,), write=True)
    recalc_points(); R.delete("cache:dash"); log(u["sub"], "reopen_match", {"match": mid}); return {"ok": True}

@app.post("/api/matches/{mid}/undo")
def undo(mid: int, u=Depends(need("admin", "scorer"))):
    if not q("SELECT 1 FROM matches WHERE match_id=%s AND status='Live'", (mid,)): raise HTTPException(400, "Match is not live")
    last = balls.find_one({"match_id": mid}, sort=[("ts", -1)])
    if not last: raise HTTPException(400, "No balls to undo")
    balls.delete_one({"_id": last["_id"]}); rebuild_live(mid)
    log(u["sub"], "undo_ball", {"match": mid, "ball": last.get("commentary")}); return {"ok": True, "undone": last.get("commentary", "")}

class TeamIn(BaseModel):
    team_name: str; short_code: str; college: str = "Manipal University Jaipur"

@app.post("/api/teams")
def add_team(b: TeamIn, u=Depends(need("admin"))):
    i = q("INSERT INTO teams (team_name, short_code, college) VALUES (%s,%s,%s)", (b.team_name, b.short_code[:3], b.college), write=True)
    q("INSERT INTO points_table (team_id) VALUES (%s)", (i,), write=True); log(u["sub"], "add_team", {"team": b.team_name}); return {"team_id": i}

@app.delete("/api/teams/{tid}")
def del_team(tid: int, u=Depends(need("admin"))):
    if q("SELECT 1 FROM matches WHERE team1_id=%s OR team2_id=%s LIMIT 1", (tid, tid)): raise HTTPException(400, "This team has matches. Delete those first.")
    if q("SELECT 1 FROM players WHERE team_id=%s LIMIT 1", (tid,)): raise HTTPException(400, "Remove the team's players first.")
    q("DELETE FROM points_table WHERE team_id=%s", (tid,), write=True); q("DELETE FROM teams WHERE team_id=%s", (tid,), write=True)
    log(u["sub"], "delete_team", {"team": tid}); return {"ok": True}

class PlayerIn(BaseModel):
    team_id: int; full_name: str; role: str; jersey_no: int

@app.post("/api/players")
def add_player(b: PlayerIn, u=Depends(need("admin"))):
    if q("SELECT COUNT(*) n FROM players WHERE team_id=%s", (b.team_id,))[0]["n"] >= 15: raise HTTPException(400, "A squad can have at most 15 players")
    i = q("INSERT INTO players (team_id, full_name, role, jersey_no) VALUES (%s,%s,%s,%s)", (b.team_id, b.full_name, b.role, b.jersey_no), write=True)
    log(u["sub"], "add_player", {"player": b.full_name}); return {"player_id": i}

@app.delete("/api/players/{pid}")
def del_player(pid: int, u=Depends(need("admin"))):
    if balls.count_documents({"$or": [{"batter_id": pid}, {"bowler_id": pid}]}): raise HTTPException(400, "This player has match records and cannot be removed.")
    q("DELETE FROM players WHERE player_id=%s", (pid,), write=True); log(u["sub"], "delete_player", {"player": pid}); return {"ok": True}


# ================= v4: tie handling, user accounts, CSV export =================
ensure_col("points_table", "tied", "INT NOT NULL DEFAULT 0")
q("""CREATE OR REPLACE VIEW v_points_standings AS
     SELECT t.team_name, p.played, p.won, p.lost, p.tied, p.points,
            ROUND(CASE WHEN p.played = 0 THEN 0 ELSE p.won * 100 / p.played END, 1) AS win_percent
     FROM points_table p JOIN teams t ON t.team_id = p.team_id ORDER BY p.points DESC, p.won DESC""", write=True)

class TieIn(BaseModel):
    super_over_winner: int | None = None

@app.post("/api/matches/{mid}/tie")
def tie(mid: int, b: TieIn, u=Depends(need("admin"))):
    if not q("SELECT 1 FROM matches WHERE match_id=%s AND status='Live'", (mid,)): raise HTTPException(400, "Only a live match can be tied")
    if b.super_over_winner:
        q("CALL complete_match(%s,%s)", (mid, b.super_over_winner), write=True)
        q("UPDATE matches SET result_note='Tie, won in super over' WHERE match_id=%s", (mid,), write=True)
    else:
        q("UPDATE matches SET status='Tied', result_note='Match tied' WHERE match_id=%s", (mid,), write=True); recalc_points()
    R.delete("cache:dash"); log(u["sub"], "tie_match", {"match": mid, "super_over_winner": b.super_over_winner})
    R.publish(f"live:{mid}", json.dumps(snapshot(mid), default=str)); return {"ok": True}

def hash_pw(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()

class UserIn(BaseModel):
    username: str; password: str; role: str = "scorer"

@app.get("/api/users")
def list_users(u=Depends(need("admin"))):
    return q("SELECT user_id, username, role FROM users ORDER BY user_id")

@app.post("/api/users")
def add_user(b: UserIn, u=Depends(need("admin"))):
    if len(b.username.strip()) < 3 or len(b.password) < 6: raise HTTPException(400, "Username needs 3+ characters and password 6+")
    if b.role not in ("admin", "scorer"): raise HTTPException(400, "Role must be admin or scorer")
    i = q("INSERT INTO users(username,password_hash,role) VALUES(%s,%s,%s)", (b.username.strip(), hash_pw(b.password), b.role), write=True)
    log(u["sub"], "create_user", {"username": b.username, "role": b.role}); return {"user_id": i}

@app.delete("/api/users/{uid}")
def del_user(uid: int, u=Depends(need("admin"))):
    row = q("SELECT username, role FROM users WHERE user_id=%s", (uid,))
    if not row: raise HTTPException(404, "User not found")
    if row[0]["username"] == u["sub"]: raise HTTPException(400, "You cannot delete your own account")
    if row[0]["role"] == "admin" and q("SELECT COUNT(*) n FROM users WHERE role='admin'")[0]["n"] <= 1: raise HTTPException(400, "Keep at least one admin")
    q("DELETE FROM users WHERE user_id=%s", (uid,), write=True); log(u["sub"], "delete_user", {"username": row[0]["username"]}); return {"ok": True}

class PwIn(BaseModel):
    password: str

@app.post("/api/users/{uid}/reset")
def reset_user(uid: int, b: PwIn, u=Depends(need("admin"))):
    if len(b.password) < 6: raise HTTPException(400, "Password needs 6+ characters")
    q("UPDATE users SET password_hash=%s WHERE user_id=%s", (hash_pw(b.password), uid), write=True)
    log(u["sub"], "reset_password", {"user": uid}); return {"ok": True}

def csv_resp(rows, name, cols=None):
    out = io.StringIO()
    if rows:
        w = csv.DictWriter(out, fieldnames=cols or list(rows[0].keys()), extrasaction="ignore", restval="")
        w.writeheader(); w.writerows(rows)
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

@app.get("/api/export/standings.csv")
def export_standings():
    return csv_resp(q("SELECT * FROM v_points_standings"), "standings.csv")

@app.get("/api/export/matches.csv")
def export_matches():
    return csv_resp(matches(), "matches.csv")

@app.get("/api/matches/{mid}/balls.csv")
def export_balls(mid: int):
    rows = list(balls.find({"match_id": mid}, {"_id": 0}).sort([("innings", 1), ("over", 1), ("ball", 1)]))
    for r in rows: r["ts"] = str(r.get("ts", ""))
    return csv_resp(rows, f"match_{mid}_balls.csv", ["match_id", "innings", "over", "ball", "batter_name", "bowler_name", "runs",
                                                     "extra_type", "is_wicket", "wicket_type", "commentary", "ts"])
