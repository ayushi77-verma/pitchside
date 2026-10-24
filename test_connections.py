import os, mysql.connector, redis
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv(override=True)

mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
for name, val in [("MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD")),
                  ("MONGO_URI", mongo_uri),
                  ("REDIS_HOST", os.getenv("REDIS_HOST")),
                  ("REDIS_PORT", os.getenv("REDIS_PORT")),
                  ("REDIS_PASSWORD", os.getenv("REDIS_PASSWORD"))]:
    print(name, "-> found" if val else "-> MISSING")

db = mysql.connector.connect(host="localhost", user="root",
     password=os.getenv("MYSQL_PASSWORD"), database="pitchside")
print("MySQL OK:", db.is_connected())

m = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
print("MongoDB OK:", m.admin.command("ping"))

r = redis.Redis(host=os.getenv("REDIS_HOST"), port=int(os.getenv("REDIS_PORT")),
     password=os.getenv("REDIS_PASSWORD"), decode_responses=True)
print("Redis OK:", r.ping())