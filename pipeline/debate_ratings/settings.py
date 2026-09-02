import datetime
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/debate")
SHEET_ID = os.environ.get("SHEET_ID", "167zz_2rI83PXN_oZkIG5fuvVuKxnVtuNL6v0F_8hKJE")
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
MIN_AGE_DAYS = int(os.environ.get("MIN_AGE_DAYS", "7"))
QUALITY_PRIOR_STRENGTH = float(os.environ.get("QUALITY_PRIOR_STRENGTH", "0"))
MAX_ROOM_DROP = float(os.environ.get("MAX_ROOM_DROP", "0.05"))
MAX_SPEAKER_DROP = float(os.environ.get("MAX_SPEAKER_DROP", "0.02"))
EPOCH = datetime.date(2010, 1, 1)
USER_AGENT = "Mozilla/5.0 (compatible; debate-ratings-bot)"
