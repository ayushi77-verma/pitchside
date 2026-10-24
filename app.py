import os, time
from datetime import datetime
import streamlit as st, pandas as pd, plotly.express as px
import mysql.connector, redis
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv(override=True)

st.set_page_config(page_title="PitchSide", page_icon="🏏", layout="wide")
st.markdown("""<style>
.stApp{background:radial-gradient(circle at 15% 0%,#12263f 0%,#0a1020 45%,#070b16 100%)}
.hero{padding:26px 30px;border-radius:20px;margin-bottom:18px;background:linear-gradient(120deg,#00b386 0%,#0b6cff 60%,#7a2cff 100%)}
.hero h1{margin:0;color:#fff;font-size:2.2rem}.hero p{margin:4px 0 0;color:#e8f6ff}
.card{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:16px 20px;margin-bottom:10px}
.kpi{font-size:2rem;font-weight:800;color:#fff}.lbl{color:#8fa3c4;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase}
.score{font-size:3.2rem;font-weight:800;color:#fff;line-height:1.1}
.chip{display:inline-block;width:38px;height:38px;line-height:38px;text-align:center;border-radius:50%;margin-right:6px;font-weight:700;color:#fff}
.live{display:inline-block;padding:2px 12px;border-radius:99px;background:#ff3b5c;color:#fff;font-size:.75rem;font-weight:700;animation:p 1.4s infinite}
@keyframes p{50%{opacity:.35}}
</style>""", unsafe_allow_html=True)

# ---------- connections ----------
@st.cache_resource
def balls_col():
    return MongoClient(os.getenv("MONGO_URI") or os.getenv("MONGODB_URI"))["pitchside_live"]["balls"]

@st.cache_resource
def rds():
    return redis.Redis(host=os.getenv("REDIS_HOST"), port=int(os.getenv("REDIS_PORT")),
                       password=os.getenv("REDIS_PASSWORD"), decode_responses=True)

def sql(q, params=(), write=False):
    conn = mysql.connector.connect(host="localhost", user="root",
           password=os.getenv("MYSQL_PASSWORD"), database="pitchside")
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(q, params)
        if write:
            conn.commit()
            return cur.lastrowid
        return cur.fetchall()
    finally:
        conn.close()

# ---------- small helpers ----------
def hero(title, sub):
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{sub}</p></div>', unsafe_allow_html=True)

def kpi(label, value, note=""):
    return f'<div class="card"><div class="lbl">{label}</div><div class="kpi">{value}</div><div class="lbl">{note}</div></div>'

def overs_text(b):
    return f"{b // 6}.{b % 6}"

def all_matches(status=None):
    w = "WHERE m.status=%s" if status else ""
    return sql(f"""SELECT m.match_id,m.team1_id,m.team2_id,m.overs,m.status,t1.team_name team1,t2.team_name team2
        FROM matches m JOIN teams t1 ON t1.team_id=m.team1_id JOIN teams t2 ON t2.team_id=m.team2_id
        {w} ORDER BY m.match_id DESC""", (status,) if status else ())

def pick_match(ms, key):
    by = {m["match_id"]: m for m in ms}
    mid = st.selectbox("Match", list(by), key=key,
          format_func=lambda i: f"#{i}  {by[i]['team1']} vs {by[i]['team2']}  ({by[i]['status']})")
    return mid, by[mid]

# ---------- Redis (pipelined: ONE network trip instead of hundreds) ----------
def apply_ball(p, d):
    mid, key = d["match_id"], f"match:{d['match_id']}:inn{d['innings']}"
    p.hincrby(key, "runs", d["runs"])
    if d["extra_type"] is None:
        p.hincrby(key, "balls", 1)
        p.zincrby(f"match:{mid}:top", d["runs"], d["batter_name"])
    if d["is_wicket"]:
        p.hincrby(key, "wickets", 1)
    tag = "W" if d["is_wicket"] else ("Wd" if d["extra_type"] else str(d["runs"]))
    p.lpush(f"match:{mid}:recent", f"{tag}|{d['over']}.{d['ball']}|{d.get('commentary', '')}")
    p.ltrim(f"match:{mid}:recent", 0, 5)

def push_to_redis(d):
    p = rds().pipeline(); apply_ball(p, d); p.execute()

def rebuild_redis(mid):
    r = rds(); keys = list(r.scan_iter(f"match:{mid}:*")); p = r.pipeline()
    if keys: p.delete(*keys)
    for d in balls_col().find({"match_id": mid}).sort([("innings", 1), ("over", 1), ("ball", 1)]):
        apply_ball(p, d)
    p.execute()

# ---------- PAGE: command centre ----------
def page_home():
    hero("🏏 PitchSide Command Centre", "Live cricket analytics · MySQL + MongoDB + Redis working together")
    n_m = sql("SELECT COUNT(*) n FROM matches")[0]["n"]
    n_p = sql("SELECT COUNT(*) n FROM players")[0]["n"]
    col = balls_col()
    n_b = col.count_documents({})
    n_6 = col.count_documents({"runs": 6, "extra_type": None})
    for c, (l, v, n) in zip(st.columns(4), [("Matches", n_m, "MySQL"), ("Players", n_p, "MySQL"),
                                           ("Balls recorded", n_b, "MongoDB"), ("Sixes hit", n_6, "MongoDB")]):
        c.markdown(kpi(l, v, n), unsafe_allow_html=True)
    a, b = st.columns(2)
    pts = pd.DataFrame(sql("SELECT * FROM v_points_standings"))
    a.subheader("🏆 Points table (MySQL view)")
    a.dataframe(pts, hide_index=True, use_container_width=True)
    cap = list(col.aggregate([{"$match": {"extra_type": None}},
        {"$group": {"_id": "$batter_name", "runs": {"$sum": "$runs"}}},
        {"$sort": {"runs": -1}}, {"$limit": 6}]))
    b.subheader("🧢 Orange cap race (MongoDB)")
    if cap:
        cdf = pd.DataFrame(cap).rename(columns={"_id": "batter"})
        fig = px.bar(cdf, x="runs", y="batter", orientation="h", color="runs",
                     color_continuous_scale="Tealgrn", template="plotly_dark")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False,
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=330)
        b.plotly_chart(fig, use_container_width=True)
    st.subheader("📅 Recent matches")
    st.dataframe(pd.DataFrame(all_matches()).drop(columns=["team1_id", "team2_id"]).head(8),
                 hide_index=True, use_container_width=True)

# ---------- PAGE: live match centre ----------
COL = {"W": "#ff3b5c", "6": "#7a2cff", "4": "#0b6cff", "0": "#44506a", "Wd": "#e69a00"}

@st.fragment(run_every=3)
def live_board(m):
    mid = m["match_id"]; p = rds().pipeline()
    p.hgetall(f"match:{mid}:inn1"); p.hgetall(f"match:{mid}:inn2")
    p.zrevrange(f"match:{mid}:top", 0, 4, withscores=True); p.lrange(f"match:{mid}:recent", 0, 5)
    s1, s2, top, recent = p.execute()
    g = lambda s: (int(s.get("runs", 0)), int(s.get("wickets", 0)), int(s.get("balls", 0)))
    (r1, w1, b1), (r2, w2, b2) = g(s1), g(s2)
    if m["status"] == "Live":
        st.markdown('<span class="live">● LIVE</span>', unsafe_allow_html=True)
    for c, (team, r, w, b, i) in zip(st.columns(2), [(m["team1"], r1, w1, b1, 1), (m["team2"], r2, w2, b2, 2)]):
        rr = round(r * 6 / b, 2) if b else 0.0
        c.markdown(f'<div class="card"><div class="lbl">{team} · innings {i}</div><div class="score">{r}/{w}</div>'
                   f'<div class="lbl">{overs_text(b)} / {m["overs"]} overs · run rate {rr}</div></div>', unsafe_allow_html=True)
    if r2 or b2:
        need, left = r1 + 1 - r2, m["overs"] * 6 - b2
        st.info(f"🎯 Target {r1 + 1} · {m['team2']} need {need} from {left} balls (required rate {round(need * 6 / left, 2)})"
                if need > 0 and left > 0 else "🎯 Chase finished")
    x, y = st.columns([3, 2])
    chips = "".join(f'<span class="chip" style="background:{COL.get(t.split("|")[0], "#00b386")}">{t.split("|")[0]}</span>'
                    for t in reversed(recent))
    x.markdown(f'<div class="card"><div class="lbl">Last 6 balls</div>{chips or "—"}</div>', unsafe_allow_html=True)
    y.markdown('<div class="lbl">Top scorers (Redis sorted set)</div>', unsafe_allow_html=True)
    y.dataframe(pd.DataFrame([(n, int(s)) for n, s in top], columns=["Batter", "Runs"]), hide_index=True)

def page_live():
    hero("📡 Live Match Centre", "Scores served from Redis in milliseconds · refreshes every 3 seconds")
    ms = all_matches()
    if not ms:
        st.info("Create a match first."); return
    mid, m = pick_match(ms, "live_m")
    if st.button("♻️ Rebuild scoreboard from MongoDB"):
        t = time.perf_counter(); rebuild_redis(mid)
        st.success(f"Rebuilt in {round((time.perf_counter() - t) * 1000)} ms")
    live_board(m)

# ---------- PAGE: score a ball ----------
def page_score():
    hero("🎯 Score a Ball", "Every click saves a document in MongoDB and updates Redis instantly")
    ms = all_matches("Live")
    if not ms:
        st.info("No live match. Create one in the Matches page."); return
    mid, m = pick_match(ms, "score_m")
    inn = st.radio("Innings", [1, 2], horizontal=True)
    bat_id, bowl_id = (m["team1_id"], m["team2_id"]) if inn == 1 else (m["team2_id"], m["team1_id"])
    q = "SELECT player_id, full_name FROM players WHERE team_id=%s ORDER BY jersey_no"
    bat = {p["player_id"]: p["full_name"] for p in sql(q, (bat_id,))}
    bowl = {p["player_id"]: p["full_name"] for p in sql(q, (bowl_id,))}
    col = balls_col()
    legal = col.count_documents({"match_id": mid, "innings": inn, "extra_type": None})
    wk = col.count_documents({"match_id": mid, "innings": inn, "is_wicket": True})
    st.markdown(kpi(f"Innings {inn}", f"{overs_text(legal)} / {m['overs']} overs", f"{wk} wickets"), unsafe_allow_html=True)
    last = col.find_one({"match_id": mid, "innings": inn}, sort=[("ts", -1)])
    if last: st.info("Last ball: " + last.get("commentary", ""))
    if legal >= m["overs"] * 6 or wk >= len(bat) - 1:
        st.warning("This innings is finished."); return
    c1, c2, c3 = st.columns(3)
    striker = c1.selectbox("Striker", list(bat), format_func=bat.get)
    bowler = c2.selectbox("Bowler", list(bowl), format_func=bowl.get)
    wtype = c3.selectbox("If wicket, how out?", ["bowled", "caught", "lbw", "run out"])
    labels, clicked = ["0", "1", "2", "3", "4", "6", "W", "Wd"], None
    for c, lab in zip(st.columns(8), labels):
        if c.button(lab, key=f"b{lab}", use_container_width=True): clicked = lab
    if clicked:
        d = {"match_id": mid, "innings": inn, "over": legal // 6 + 1, "ball": legal % 6 + 1, "batting_team_id": bat_id,
             "batter_id": striker, "batter_name": bat[striker], "bowler_id": bowler, "bowler_name": bowl[bowler],
             "runs": 0, "extra_type": None, "is_wicket": False, "wicket_type": None, "ts": datetime.now()}
        if clicked == "Wd": d.update(runs=1, extra_type="wide", commentary="Wide ball")
        elif clicked == "W": d.update(is_wicket=True, wicket_type=wtype, commentary=f"OUT! {bat[striker]} {wtype}")
        else:
            r_ = int(clicked)
            d.update(runs=r_, commentary="SIX!" if r_ == 6 else "FOUR!" if r_ == 4 else f"{r_} run(s)")
        col.insert_one(d); push_to_redis(d); st.rerun()

# ---------- PAGE: matches ----------
def page_matches():
    hero("📅 Match Control", "MySQL enforces the rules: constraints, stored procedure, trigger")
    names = {t["team_id"]: t["team_name"] for t in sql("SELECT team_id, team_name FROM teams ORDER BY team_name")}
    venue = sql("SELECT venue_id FROM venues")[0]["venue_id"]
    with st.form("nm"):
        a = st.selectbox("Team 1", list(names), format_func=names.get)
        b = st.selectbox("Team 2", list(names), index=1, format_func=names.get)
        ov = st.number_input("Overs", 1, 20, 5)
        if st.form_submit_button("Create live match"):
            try:
                sql("INSERT INTO matches (team1_id,team2_id,venue_id,match_date,overs,status) VALUES (%s,%s,%s,CURDATE(),%s,'Live')",
                    (a, b, venue, int(ov)), write=True); st.success("Match created")
            except mysql.connector.Error as e:
                st.error(f"MySQL rejected it: {e.msg}")
    st.subheader("Finish a match")
    live = all_matches("Live")
    if live:
        mid, m = pick_match(live, "fin_m")
        w = st.radio("Winner", [m["team1_id"], m["team2_id"]], horizontal=True,
                     format_func=lambda i: m["team1"] if i == m["team1_id"] else m["team2"])
        if st.button("Complete match"):
            try:
                sql("CALL complete_match(%s,%s)", (mid, w), write=True)
                st.success("Completed. The trigger updated the points table.")
            except mysql.connector.Error as e:
                st.error(f"MySQL says: {e.msg}")

# ---------- PAGE: analytics ----------
def page_stats():
    hero("📊 Analytics", "MongoDB aggregation pipelines over ball-by-ball documents")
    ms = all_matches()
    if not ms:
        st.info("No matches yet."); return
    mid, m = pick_match(ms, "st_m")
    col = balls_col()
    per = list(col.aggregate([{"$match": {"match_id": mid}},
        {"$group": {"_id": {"i": "$innings", "o": "$over"}, "runs": {"$sum": "$runs"}}}, {"$sort": {"_id.i": 1, "_id.o": 1}}]))
    if not per:
        st.info("No balls recorded for this match yet."); return
    pdf = pd.DataFrame([{"innings": f"Innings {x['_id']['i']}", "over": x["_id"]["o"], "runs": x["runs"]} for x in per])
    pdf["total"] = pdf.groupby("innings")["runs"].cumsum()
    style = dict(template="plotly_dark")
    a, b = st.columns(2)
    f1 = px.bar(pdf, x="over", y="runs", color="innings", barmode="group", title="Manhattan: runs per over", **style)
    f2 = px.line(pdf, x="over", y="total", color="innings", markers=True, title="Worm: cumulative runs", **style)
    for f in (f1, f2): f.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    a.plotly_chart(f1, use_container_width=True); b.plotly_chart(f2, use_container_width=True)
    bat = pd.DataFrame(list(col.aggregate([{"$match": {"match_id": mid, "extra_type": None}},
        {"$group": {"_id": "$batter_name", "runs": {"$sum": "$runs"}, "balls": {"$sum": 1},
         "fours": {"$sum": {"$cond": [{"$eq": ["$runs", 4]}, 1, 0]}}, "sixes": {"$sum": {"$cond": [{"$eq": ["$runs", 6]}, 1, 0]}}}},
        {"$sort": {"runs": -1}}]))).rename(columns={"_id": "batter"})
    bat["strike_rate"] = (bat.runs * 100 / bat.balls).round(1)
    bow = pd.DataFrame(list(col.aggregate([{"$match": {"match_id": mid}},
        {"$group": {"_id": "$bowler_name", "runs": {"$sum": "$runs"},
         "balls": {"$sum": {"$cond": [{"$eq": ["$extra_type", None]}, 1, 0]}},
         "wickets": {"$sum": {"$cond": ["$is_wicket", 1, 0]}}}}]))).rename(columns={"_id": "bowler"})
    bow["economy"] = (bow.runs * 6 / bow.balls.replace(0, pd.NA)).astype(float).round(2)
    c, d = st.columns(2)
    c.subheader("Batting"); c.dataframe(bat, hide_index=True, use_container_width=True)
    d.subheader("Bowling"); d.dataframe(bow.sort_values("wickets", ascending=False), hide_index=True, use_container_width=True)
    wk = pd.DataFrame(list(col.aggregate([{"$match": {"match_id": mid, "is_wicket": True}},
        {"$group": {"_id": "$wicket_type", "n": {"$sum": 1}}}])))
    if not wk.empty:
        fw = px.pie(wk, names="_id", values="n", hole=.5, title="How wickets fell", **style)
        fw.update_layout(paper_bgcolor="rgba(0,0,0,0)"); st.plotly_chart(fw, use_container_width=True)

# ---------- PAGE: system health ----------
def ms_of(fn):
    t = time.perf_counter(); fn(); return round((time.perf_counter() - t) * 1000)

def page_health():
    hero("🛠️ System Health", "Three databases, three jobs. Live latency and index proof")
    lat = [("MySQL", ms_of(lambda: sql("SELECT 1")), "Strict data · rules · trigger · procedure"),
           ("MongoDB Atlas", ms_of(lambda: balls_col().database.command("ping")), "Flexible ball-by-ball documents"),
           ("Redis Cloud", ms_of(lambda: rds().ping()), "Instant live scoreboard")]
    for c, (n, v, note) in zip(st.columns(3), lat):
        c.markdown(kpi(n, f"{v} ms", note), unsafe_allow_html=True)
    st.subheader("MySQL index proof (EXPLAIN)")
    st.caption("The query below uses idx_matches_date_status instead of scanning every row.")
    st.dataframe(pd.DataFrame(sql("EXPLAIN SELECT * FROM matches WHERE match_date=CURDATE() AND status='Live'")))
    st.subheader("Data flow")
    st.code("Click a ball → MongoDB (permanent record) → Redis (live counters) → Scoreboard\n"
            "Finish match → MySQL stored procedure (transaction) → trigger → points table → view", language="text")
    st.write("Redis keys stored:", rds().dbsize(), " · Ball documents in MongoDB:", balls_col().count_documents({}))

PAGES = {"Command Centre": page_home, "Live Match Centre": page_live, "Score a Ball": page_score,
         "Match Control": page_matches, "Analytics": page_stats, "System Health": page_health}
st.sidebar.title("🏏 PitchSide")
PAGES[st.sidebar.radio("Navigate", list(PAGES))]()