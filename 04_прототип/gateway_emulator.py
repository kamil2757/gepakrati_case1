import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
GATEWAY_LOG = BASE_DIR / "gateway.log"


def emulate_gateway(order_id, *, timeout_seconds=600, log_path=GATEWAY_LOG):
    
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
        "timestamp": datetime.now(timezone.utc).strftime(
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
