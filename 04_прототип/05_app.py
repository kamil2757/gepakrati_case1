#!/usr/bin/env python3
"""
CLI-прототип платёжного сервиса — кейс 1.

Использование:
    python 05_app.py create <файл.json> <idempotency_key>
    python 05_app.py confirm <payment_id> <confirm_token>
    python 05_app.py status <payment_id>
    python 05_app.py cancel <payment_id>
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
from gateway_emulator import emulate_gateway

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPIENTS_CSV = os.path.join(BASE_DIR, "01_данные", "recipients.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")
EVENTS_LOG = os.path.join(BASE_DIR, 'events.log')
TOKENS = os.path.join(BASE_DIR, 'tokens.json')

fieldnames = [
    "payment_id", "order_id", "recipient", "purpose", "amount",
    "currency", "status", "error_code", "idempotency_key",
    "created_at", "confirmed_at", "correlation_id"
]


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
    """Реализовать V-1...V-9. Вернуть [(field, code), ...]."""
    errors = []
    # V-1
    for field in ["order_id", "recipient", "purpose", "amount", "currency"]:
        if payload.get(field) in (None, ""):
            errors.append((field, "VALIDATION_ERROR"))

    # V-2
    order_id_val = payload.get('order_id')
    if order_id_val is not None:
        pattern = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
        if not pattern.match(str(order_id_val)):
            errors.append(('order_id', 'INVALID_FORMAT'))

    # V-3
    if "amount" in payload:
        try:
            amt = float(payload["amount"])
            if amt <= 0 or amt > 1_000_000:
                errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))
        except (ValueError, TypeError):
            errors.append(("amount", "INVALID_FORMAT"))

    # V-4
    if payload.get('currency') != 'RUB':
        errors.append(('currency', 'UNSUPPORTED_CURRENCY'))

    # V-5
    recipient = payload.get('recipient')
    if recipient and recipient not in active_recipients:
        errors.append(('recipient', 'RECIPIENT_NOT_FOUND'))

    # V-6
    purpose = str(payload.get('purpose', ''))
    pattern_pan = re.compile(r'\d{13,19}')
    if len(purpose) > 200 or pattern_pan.search(purpose):
        errors.append(('purpose', 'INVALID_PURPOSE'))

    # V-7
    amount = payload.get('amount')
    if len(str(amount).partition(".")[2]) > 2:
        errors.append(('amount', 'INVALID_AMOUNT_PRECISION'))

    # V-8
    order_id = payload.get('order_id')
    recipient = payload.get('recipient')
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('order_id') == order_id and row.get('recipient') == recipient:
                    errors.append(('order_id', 'DUPLICATE_ORDER'))
                    break

    return errors


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
    frames = ["   ", ".  ", ".. ", "..."]
    end_time = time.time() + seconds
    i = 0
    while time.time() < end_time:
        sys.stdout.write(f"\r{frames[i % len(frames)]}")
        sys.stdout.flush()
        time.sleep(0.4)
        i += 1


def write_result(payload, status, error_code, idem_key, payment_id):
    msk_tz = datetime.timezone(datetime.timedelta(hours=3))
    time_now = datetime.datetime.now(msk_tz).strftime("%Y-%m-%dT%H:%M:%SZ")
    corr_id = "corr_" + uuid.uuid4().hex[:6]
    with open(OUT_CSV, 'a', encoding="utf-8-sig", newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            payment_id,
            payload.get("order_id"),
            payload.get("recipient"),
            payload.get("purpose"),
            payload.get("amount"),
            payload.get("currency"),
            status,
            error_code or "",
            idem_key,
            time_now,
            time_now if status == 'SUCCESS' else '',
            corr_id,
        ])


def writeLog(payment_id, status):
    with open(EVENTS_LOG, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} Платеж {payment_id}: {status}\n')


def getLogsHistory(payment_id):
    history = []
    if not os.path.exists(EVENTS_LOG):
        return history

    with open(EVENTS_LOG, 'r', encoding="utf-8-sig") as f:
        for l in f:
            if payment_id in l:
                data = l.split(' ')
                if len(data) >= 2:
                    time_str = data[0] + 'T' + data[1] + 'Z'
                    status = data[-1].strip()
                    history.append({'status': status, 'at': time_str})
    return history


def main():
    init_out_csv()
    if len(sys.argv) < 3:
        print("Как использовать:")
        print("  Создать: python 05_app.py create <файл.json> <idempotency_key>")
        print("  Подтвердить: python 05_app.py confirm <payment_id> <confirm_token>")
        print("  Статус:  python 05_app.py status <payment_id>")
        print("  Отмена:  python 05_app.py cancel <payment_id>")
        sys.exit(1)

    action = sys.argv[1]

    if action == 'create':
        if len(sys.argv) < 4:
            print('Некорректный запрос. Отсутствуют данные')
            print(json.dumps({
                'error_code': "BAD_REQUEST",
                'message': "Не удалось распарсить тело запроса: ожидался корректный JSON",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

        idem_key = sys.argv[3]

        try:
            with open(sys.argv[2], encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError
        except FileNotFoundError:
            print(json.dumps({"error_code": "BAD_REQUEST", "message": "Файл запроса не найден"}, ensure_ascii=False,
                             indent=2))
            sys.exit(400)
        except (json.JSONDecodeError, ValueError):
            print(json.dumps({"error_code": "BAD_REQUEST", "message": "Невалидный JSON"}, ensure_ascii=False, indent=2))
            sys.exit(400)

        payment_id = "pay_demo_" + uuid.uuid4().hex[:6]

        # V-9
        with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('idempotency_key') == idem_key:
                    print('Это повторный запрос. Возвращаем результат предыдущего платежа:')
                    print(json.dumps({
                        "payment_id": row.get('payment_id'),
                        "status": row.get('status'),
                        "confirm_token": "ALREADY_USED",
                        'expires_in': 540,
                        "error_code": "DUPLICATE_REQUEST"
                    }, ensure_ascii=False, indent=2))
                    sys.exit(0)

        active = load_active_recipients()
        errors = validate(payload, active, idem_key)

        if errors:
            has_DUPLICATE_ORDER = any([True if 'DUPLICATE_ORDER' in er else False for er in errors])
            has_RECIPIENT_NOT_FOUND = any([True if 'RECIPIENT_NOT_FOUND' in er else False for er in errors])
            if has_DUPLICATE_ORDER:
                print(json.dumps({
                    "error_code": "DUPLICATE_ORDER",
                    'message': "Заказ с таким идентификатором уже оплачен или обрабатывается",
                    'retryable': False
                }, ensure_ascii=False, indent=2))
                sys.exit(409)
            elif has_RECIPIENT_NOT_FOUND:
                print(json.dumps({
                    "error_code": "RECIPIENT_NOT_FOUND",
                    'message': "Указанный получатель платежа не зарегистрирован в системе",
                    'retryable': False
                }, ensure_ascii=False, indent=2))
                sys.exit(404)
            else:
                print(json.dumps({
                    "error_code": "VALIDATION_ERROR",
                    "message": "Ошибка валидации данных",
                    'retryable': False,
                    "errors": [{"field": f, "code": c} for f, c in errors],
                }, ensure_ascii=False, indent=2))
                sys.exit(422)

        status = 'CREATED'
        print(f'Платеж создан {payload.get("order_id")}, статус {status}')
        writeLog(payment_id, status)
        time.sleep(1)
        confirm_token = uuid.uuid4().hex[:6]
        print(f'Токен сгенерирован: {confirm_token}, отправлен пользователю, ждем подтверждения \n')
        print('Ожидание подтверждения клиентом ', end='\n')

        tokens_data = {}
        if os.path.exists(TOKENS):
            with open(TOKENS, 'r', encoding='utf-8-sig') as f:
                tokens_data = json.load(f)

        tokens_data[payment_id] = {
            'token': confirm_token,
            'attempts': 0,
            'created_at': time.time()
        }
        with open(TOKENS, 'w', encoding='utf-8') as f:
            json.dump(tokens_data, f, ensure_ascii=False, indent=2)

        write_result(payload, 'CREATED', '', idem_key, payment_id)
        print(json.dumps({
            "payment_id": payment_id,
            "status": status,
            'confirm_token': confirm_token,
            "expires_in": 600,
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    elif action == 'confirm':
        if len(sys.argv) < 4:
            print('Некорректный запрос. Отсутствуют данные')
            sys.exit(1)
        payment_id = sys.argv[2]
        user_token = sys.argv[3]

        tokens_data = {}
        if os.path.exists(TOKENS):
            with open(TOKENS, 'r', encoding='utf-8-sig') as f:
                tokens_data = json.load(f)

        if payment_id not in tokens_data:
            payment_already_success = False
            with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    if row.get('payment_id') == payment_id and row.get('status') == 'SUCCESS':
                        payment_already_success = True
                        break

            if payment_already_success:
                print(json.dumps({
                    "payment_id": payment_id,
                    "status": "SUCCESS",
                    "error_code": "DUPLICATE_REQUEST",
                    "message": "Платеж уже был успешно подтвержден ранее"
                }, ensure_ascii=False, indent=2))
                sys.exit(0)
            else:
                print(json.dumps({
                    "error_code": "PAYMENT_NOT_FOUND",
                    "message": "Платеж не найден или токен истек"
                }, ensure_ascii=False, indent=2))
                sys.exit(404)

        token_info = tokens_data[payment_id]

        if time.time() - token_info['created_at'] > 600:
            print(json.dumps({
                "error_code": "TOKEN_EXPIRED",
                "message": "Время действия токена истекло"
            }, ensure_ascii=False, indent=2))

            updated_rows = []
            with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    if row.get('payment_id') == payment_id:
                        row['status'] = 'DECLINED'
                        row['error_code'] = 'TOKEN_EXPIRED'
                    updated_rows.append(row)

            with open(OUT_CSV, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, delimiter=';', fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)

            writeLog(payment_id, 'DECLINED')

            del tokens_data[payment_id]
            with open(TOKENS, 'w', encoding='utf-8') as f:
                json.dump(tokens_data, f)
            sys.exit(410)

        if user_token != token_info['token']:
            token_info['attempts'] += 1

            if token_info.get('attempts') >= 3:
                print(json.dumps({
                    "error_code": "INVALID_CONFIRM_TOKEN",
                    "message": "Превышено количество попыток"
                }, ensure_ascii=False, indent=2))

                del tokens_data[payment_id]
                with open(TOKENS, 'w', encoding='utf-8') as f:
                    json.dump(tokens_data, f)

                updated_rows = []
                with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f, delimiter=';')
                    for row in reader:
                        if row.get('payment_id') == payment_id:
                            row['status'] = 'DECLINED'
                            row['error_code'] = 'INVALID_CONFIRM_TOKEN'  # <--- ДОБАВЛЕНО
                        updated_rows.append(row)
                writeLog(payment_id, 'DECLINED')

                with open(OUT_CSV, mode='w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, delimiter=';', fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(updated_rows)

                sys.exit(410)
            else:
                print(json.dumps({
                    "error_code": "INVALID_CONFIRM_TOKEN",
                    "message": f"Неверный код. Осталось попыток: {3 - token_info['attempts']}"
                }, ensure_ascii=False, indent=2))

            with open(TOKENS, 'w', encoding='utf-8') as f:
                json.dump(tokens_data, f)
            sys.exit(422)

        del tokens_data[payment_id]
        with open(TOKENS, 'w', encoding='utf-8') as f:
            json.dump(tokens_data, f)

        order_id = None
        found_row = None
        updated_rows = []
        with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('payment_id') == payment_id:
                    order_id = row.get('order_id')
                    found_row = row
                updated_rows.append(row)

        if not order_id:
            print("Ошибка: Платеж не найден в базе данных")
            sys.exit(500)

        print("Токен верный. Запрос отправлен в банк...")
        gateway_status, error_code = emulate_gateway(order_id)
        status = "PENDING" if gateway_status == "TIMEOUT" else gateway_status

        msk_tz = datetime.timezone(datetime.timedelta(hours=3))
        time_now = datetime.datetime.now(msk_tz).strftime("%Y-%m-%dT%H:%M:%SZ")

        for row in updated_rows:
            if row['payment_id'] == payment_id:
                row['status'] = status
                row['error_code'] = error_code or ""
                row['confirmed_at'] = time_now if status == 'SUCCESS' else ""

        with open(OUT_CSV, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, delimiter=';', fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(updated_rows)

        writeLog(payment_id, status)
        history = getLogsHistory(payment_id)
        print('Результат подтверждения:')
        print(json.dumps({
            "payment_id": payment_id,
            "status": status,
            "error_code": error_code,
            "amount": float(found_row['amount']),
            "currency": found_row['currency'],
            "history": history
        }, ensure_ascii=False, indent=2))

        if status == 'SUCCESS':
            sys.exit(0)
        elif error_code == "TIMEOUT" or error_code == "UNKNOWN":
            sys.exit(504)
        else:
            sys.exit(402)

    elif action == 'status':
        payment_id = sys.argv[2]
        found_row = False
        with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('payment_id') == payment_id:
                    found_row = row
                    break

        if found_row:
            history = getLogsHistory(payment_id)
            print('Информация о платеже успешно получена')
            error_code = found_row.get("error_code")
            print(json.dumps({
                "payment_id": found_row.get('payment_id'),
                "status": found_row.get('status'),
                "error_code": error_code if error_code else None,
                'amount': float(found_row.get('amount')),
                'currency': found_row.get('currency'),
                'history': history
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                'error_code': "PAYMENT_NOT_FOUND",
                'message': f"Платёж с ID {payment_id} не найден в базе данных клиники",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(404)

    elif action == 'cancel':
        payment_id = sys.argv[2]
        payment_exists = False
        canceled = False
        updated_rows = []
        founded_row = None

        with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('payment_id') == payment_id:
                    payment_exists = True
                    if row['status'] in ['CREATED', 'PENDING']:
                        row['status'] = 'CANCELED'
                        canceled = True
                        founded_row = row
                        writeLog(payment_id, 'CANCELED')
                updated_rows.append(row)

        if canceled:
            history = getLogsHistory(payment_id)
            with open(OUT_CSV, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, delimiter=';', fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)

            tokens_data = {}
            if os.path.exists(TOKENS):
                with open(TOKENS, 'r', encoding='utf-8-sig') as f:
                    tokens_data = json.load(f)

            if payment_id in tokens_data:
                del tokens_data[payment_id]
                with open(TOKENS, 'w', encoding='utf-8') as f:
                    json.dump(tokens_data, f)

            print(json.dumps({
                "payment_id": payment_id,
                "status": "CANCELED",
                "error_code": None,
                "amount": float(founded_row.get("amount")),
                "currency": founded_row.get("currency"),
                "history": history
            }, ensure_ascii=False, indent=2))
            sys.exit(0)

        elif payment_exists:
            print(json.dumps({
                'error_code': "INVALID_STATE",
                'message': "Нельзя отменить уже оплаченный или отклоненный платёж",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(409)

        else:
            print(json.dumps({
                'error_code': "PAYMENT_NOT_FOUND",
                'message': f"Платёж с ID {payment_id} не найден в базе данных",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(404)


if __name__ == "__main__":
    main()