import hashlib
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
GATEWAY_LOG = BASE_DIR / "gateway.log"
MSK_TZ = timezone(timedelta(hours=3))


def emulate_gateway(order_id, *, timeout_seconds=3, log_path=GATEWAY_LOG):
    started = time.monotonic()

    bucket = int(
        hashlib.md5(order_id.encode("utf-8")).hexdigest(), 16
    ) % 10

    if bucket <= 6:
        status, error_code = "SUCCESS", None
    elif bucket == 7:
        status, error_code = "DECLINED", "INSUFFICIENT_FUNDS"
    elif bucket == 8:
        status, error_code = "DECLINED", "CARD_DECLINED"
    else:
        time.sleep(timeout_seconds)
        status, error_code = "TIMEOUT", "UNKNOWN"

    record = {
        "timestamp": datetime.now(MSK_TZ).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "order_id": order_id,
        "status": status,
        "error_code": error_code,
        "duration(ms)": round((time.monotonic() - started) * 1000),
    }

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")

    return status, error_code


def emulate_gateway_status(order_id, *, log_path=GATEWAY_LOG):
    started = time.monotonic()

    if "decline" in order_id.lower() or "fail" in order_id.lower():
        status, error_code = "DECLINED", "CARD_DECLINED"
    else:
        status, error_code = "SUCCESS", None

    record = {
        "timestamp": datetime.now(MSK_TZ).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "order_id": order_id,
        "status": status,
        "error_code": error_code,
        "duration(ms)": round((time.monotonic() - started) * 1000),
    }

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")

    return status, error_code