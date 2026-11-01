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
import datetime
import re
import uuid
import time


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



def validate(payload, active_recipients, idem_key):
    """TODO: реализовать V-3…V-9. Вернуть [(field, code), ...]."""
    errors = []
    # ОБРАЗЕЦ — V-1
    for field in ["order_id", "recipient", "purpose", "amount", "currency"]:
        if not payload.get(field):
            errors.append((field, "VALIDATION_ERROR"))
    # V-2
    pattern = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
    if pattern.match(payload.get('order_id')) is None:
        errors.append(('order_id', 'INVALID_FORMAT'))

    # ОБРАЗЕЦ — V-3
    if "amount" in payload:
        amt = float(payload["amount"])
        if amt <= 0 or amt > 1_000_000:
            errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))

    # V-4
    if payload.get('currency') != 'RUB':
        errors.append(('currency', 'UNSUPPORTED_CURRENCY'))

    # V-5
    recipient = payload.get('recipient')
    if recipient and recipient not in active_recipients:
        errors.append(('recipient', 'RECIPIENT_NOT_FOUND'))

    # V-6
    purpose = payload.get('purpose')
    pattern = re.compile(r'\d{13,19}')
    if len(purpose) > 200 or pattern.search(purpose):
        errors.append(('purpose', 'INVALID_PURPOSE'))

    # V-7
    amount = payload.get('amount')
    if len(str(amount).partition(".")[2]) > 2:
        errors.append(('amount', 'INVALID_AMOUNT_PRECISION'))

    # V-8
    order_id = payload.get('order_id')
    recipient = payload.get('recipient')
    with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            if row.get('order_id') == order_id and row.get('recipient') == recipient:
                errors.append(('order_id, recipient', 'DUPLICATE_ORDER'))
                break

    # V-9
    # with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
    #     reader = csv.DictReader(f, delimiter=';')
    #     for row in reader:
    #         if row.get('idempotency_key') == idem_key:
    #             errors.append(('idempotency_key', 'DUPLICATE_REQUEST'))
    #             break

    return errors


def emulate_gateway(order_id):
    """TODO: 4 сценария. Вернуть (status, error_code)."""
    h = int(hashlib.md5(order_id.encode()).hexdigest(), 16) % 10
    # ОБРАЗЕЦ — 0..6 = SUCCESS
    if h <= 6:
        return "SUCCESS", None
    elif h == 7:
        return 'DECLINED', 'INSUFFICIENT_FUNDS'
    elif h == 8:
        return 'DECLINED', 'CARD_DECLINED'
    elif h == 9:
        print('Банк не отвечает. ожидание 10 минут')
        # тестировщик может менять значение времени, чтоб не ждать 600 секунд
        timeout = 600
        show_loading(timeout)
        print('время вышло')
        return 'PENDING', 'UNKNOWN'



    return "UNKNOWN", "NOT_IMPLEMENTED"


def init_out_csv():
    if not os.path.exists(OUT_CSV):
        with open(OUT_CSV, 'w', encoding="utf-8-sig", newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow([
                "payment_id", "order_id", "recipient", "purpose", "amount",
                "currency", "status", "error_code", "idempotency_key",
                "created_at", "confirmed_at", "correlation_id"
            ])




def show_loading(seconds):
    """Показывает красивую анимацию загрузки в течение указанного времени."""
    frames = ["   ", ".  ", ".. ", "..."]  # Кадры анимации
    end_time = time.time() + seconds

    i = 0
    while time.time() < end_time:
        sys.stdout.write(f"\r{frames[i % len(frames)]}")
        sys.stdout.flush()
        time.sleep(0.4)
        i += 1


def write_result(payload, status, error_code, idem_key):
    pay_id = "pay_demo_" + payload["order_id"][-4:]
    msk_tz = datetime.timezone(datetime.timedelta(hours=3))
    time_now = datetime.datetime.now(msk_tz).strftime("%Y-%m-%dT%H:%M:%SZ")
    corr_id = "corr_" + uuid.uuid4().hex[:6]
    with open(OUT_CSV, 'a', encoding="utf-8-sig", newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            pay_id,
            payload.get("order_id"),
            payload.get("recipient"),
            payload.get("purpose"),
            payload.get("amount"),
            payload.get("currency"),
            status,
            error_code or "",
            idem_key,
            time_now,
            time_now,
            corr_id,
        ])


def main():
    init_out_csv()
    # обязательно передавать idempotency_key, решили передавать через консоль, тк в консольном прототипе нет заголовков
    if len(sys.argv) < 3:
        print("как использовать программу: python 05_app.py <файл.json> [idempotency_key]")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    idem_key = sys.argv[2]

    # v-9 теперь тут
    with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            if row.get('idempotency_key') == idem_key:
                print('Это повторный запрос. Возвращаем результат предыдущего платежа:')
                print(json.dumps({
                    "payment_id": row.get('payment_id'),
                    "status": row.get('status'),
                    "error_code": "DUPLICATE_REQUEST"
                }, ensure_ascii=False, indent=2))
                sys.exit(0)
                break


    active = load_active_recipients()
    errors = validate(payload, active, idem_key)


    if errors:
        print(json.dumps({
            "status": "ERROR",
            "error_code": "VALIDATION_ERROR",
            "errors": [{"field": f, "code": c} for f, c in errors],
        }, ensure_ascii=False, indent=2))
        write_result(payload, 'ERROR', 'VALIDATION_ERROR', idem_key)
        sys.exit(2)

    print(f'Платеж создан {payload.get("order_id")}, статус CREATED')
    time.sleep(1)
    confirm_token = uuid.uuid4().hex[:6]
    print(f'Токен сгенерировал: {confirm_token}, оправлен пользователю, ждем подтверждения \n')
    print('Ожидание подтверждения клиентом ', end='\n')
    show_loading(5)
    print(f'Токен подтвержден пользователем, статус PENDING')
    time.sleep(1)
    print()
    print('Запрос отправлен в банк')
    show_loading(2)

    status, error_code = emulate_gateway(payload["order_id"])
    write_result(payload, status, error_code, idem_key)

    print('Результат:')
    print(json.dumps({
        "payment_id": "pay_demo_" + payload["order_id"][-4:],
        "status": status,
        "error_code": error_code,
    }, ensure_ascii=False, indent=2))

    sys.exit(0 if status == "SUCCESS" else (3 if error_code == "UNKNOWN" else 1))


if __name__ == "__main__":
    main()
