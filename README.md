# 🏏 PitchSide: Live Cricket Tournament Platform

🔗 **Live showcase (try the simulator):** https://ayushi77-verma.github.io/pitchside/

A real-time scoring and analytics platform for inter-college cricket tournaments, built for the RDBMS course (Fast Learner project).
Three databases, each doing the job it is best at.

| Database | Role |
|---|---|
| **MySQL** | Teams, players, fixtures, users. Constraints, **view**, **trigger**, **stored procedure** (transaction), **index** |
| **MongoDB Atlas** | Every ball as a document, aggregation pipelines for analytics, admin audit log |
| **Redis Cloud** | Live scoreboard counters, sorted-set leaderboard, **pub/sub** push over WebSocket, caching, rate limiting |

**Backend:** FastAPI (JWT login, role-based access, auto docs at `/docs`) · **Frontend:** single-page HTML/JS with Chart.js

## Features
Fixtures with toss, live scorer console (keyboard shortcuts, undo), instant live scoreboard, points table updated by a MySQL trigger,
edit / delete / reopen / abandon / tie (super over), team and squad management, player of the match, analytics charts,
admin-created scorer accounts, audit trail, CSV export, system health with EXPLAIN index proof.

## Run it
1. Install **Python 3.12**, **MySQL 8** and create free **MongoDB Atlas** and **Redis Cloud** databases.
2. Clone and install:
   ```
   git clone https://github.com/YOUR-USERNAME/pitchside.git
   cd pitchside
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your values.
4. In MySQL: `CREATE DATABASE pitchside;` then run `database/schema.sql` and `database/advanced.sql`.
5. Start: `uvicorn main:app --reload` and open **http://localhost:8000** (API docs: `/docs`).
6. Demo data: start the server once, then run `python seed_tournament.py` and type YES. It creates 6 teams, 66 players, 10 results, one live match and upcoming fixtures.

Default logins (change them): `admin / admin@123`, `scorer / scorer@123`.

## Project layout
```
main.py          FastAPI backend (REST + WebSocket)
index.html       Frontend
seed_tournament.py  Demo tournament generator
docs/            GitHub Pages showcase site
database/        SQL scripts
```
