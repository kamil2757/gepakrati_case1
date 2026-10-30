#!/usr/bin/env python3
"""
CLI-прототип платёжного сервиса — кейс 1.

Использование:
    python 04_прототип.py payment.json

TODO для команды:
    1. Дописать validate() — правила V-3…V-9 из 02_правила.md.
    2. Дописать emulate_gateway() — 4 сценария (SUCCESS / INSUFFICIENT_FUNDS /
       CARD_DECLINED / TIMEOUT).
    3. Добавить чтение recipients.csv для проверки recipient.
    4. Обработать таймаут: exit code 3.
    5. Записать результат в payments_out.csv (append).
"""
import csv
import hashlib
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPIENTS_CSV = os.path.join(BASE_DIR, "01_данные", "recipients.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")


def load_active_recipients():
    """Прочитать recipients.csv, вернуть set() активных ID."""
    active = set()
    try:
        with open(RECIPIENTS_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "ACTIVE":
                    active.add(row["recipient_id"])
    except FileNotFoundError:
        print(f"WARN: {RECIPIENTS_CSV} не найден", file=sys.stderr)
    return active


def validate(payload, active_recipients):
    """TODO: реализовать V-3…V-9. Вернуть [(field, code), ...]."""
    errors = []
    # ОБРАЗЕЦ — V-1, V-2
    for field in ["order_id", "recipient", "purpose", "amount", "currency"]:
        if not payload.get(field):
            errors.append((field, "VALIDATION_ERROR"))
    if "amount" in payload:
        amt = float(payload["amount"])
        if amt <= 0 or amt > 1_000_000:
            errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))
    # TODO: V-3 (order_id regex), V-4 (currency), V-5 (recipient),
    #       V-6 (purpose), V-7 (2 знака), V-8 (дубль), V-9 (idempotency)
    return errors


def emulate_gateway(order_id):
    """TODO: 4 сценария. Вернуть (status, error_code)."""
    h = int(hashlib.md5(order_id.encode()).hexdigest(), 16) % 10
    # ОБРАЗЕЦ — 0..6 = SUCCESS
    if h <= 6:
        return "SUCCESS", None
    # TODO: 7 = INSUFFICIENT_FUNDS, 8 = CARD_DECLINED, 9 = TIMEOUT
    return "UNKNOWN", "NOT_IMPLEMENTED"


def main():
    if len(sys.argv) < 2:
        print("Использование: python 04_прототип.py payment.json")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    active = load_active_recipients()
    errors = validate(payload, active)

    if errors:
        print(json.dumps({
            "status": "ERROR",
            "error_code": "VALIDATION_ERROR",
            "errors": [{"field": f, "code": c} for f, c in errors],
        }, ensure_ascii=False, indent=2))
        sys.exit(2)

    status, error_code = emulate_gateway(payload["order_id"])
    print(json.dumps({
        "payment_id": "pay_demo_" + payload["order_id"][-4:],
        "status": status,
        "error_code": error_code,
    }, ensure_ascii=False, indent=2))

    sys.exit(0 if status == "SUCCESS" else (3 if status == "TIMEOUT" else 1))


if __name__ == "__main__":
    main()