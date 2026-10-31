
import os
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

secret = os.getenv("NOTIFICATION_JOB_SECRET", "")

if not secret:
    raise SystemExit(
        "NOTIFICATION_JOB_SECRET이 .env에 없습니다."
    )

base_url = os.getenv(
    "NOTIFICATION_BASE_URL",
    "http://127.0.0.1:8000"
).rstrip("/")

request = Request(
    f"{base_url}/notifications/jobs/run",
    data=b"{}",
    method="POST",
    headers={
        "Content-Type": "application/json",
        "x-job-secret": secret,
    },
)

with urlopen(request, timeout=35) as response:
    print(response.read().decode("utf-8"))
