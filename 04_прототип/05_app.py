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
from gateway_emulator import emulate_gateway



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPIENTS_CSV = os.path.join(BASE_DIR, "01_данные", "recipients.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")
EVENTS_LOG = os.path.join(BASE_DIR, 'events.log')


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
                errors.append(('order_id', 'DUPLICATE_ORDER'))
                break

    # V-9
    # with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:
    #     reader = csv.DictReader(f, delimiter=';')
    #     for row in reader:
    #         if row.get('idempotency_key') == idem_key:
    #             errors.append(('idempotency_key', 'DUPLICATE_REQUEST'))
    #             break

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
            time_now if status == 'SUCCESS' else '',
            corr_id,
        ])



def writeLog(payment_id, status):
    with open(EVENTS_LOG, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} Платеж {payment_id}: {status}\n')


def getLogsHistory(payment_id):
    history = []
    with open(EVENTS_LOG, 'r', encoding="utf-8-sig") as f:
        for l in f:
            if payment_id in l:
                data = l.split(' ')
                time = data[0] + 'T' + data[1] + 'Z'
                status = data[-1][:-1]

                history.append({'status': status, 'at': time})
    return history


def main():
    init_out_csv()
    # обязательно передавать idempotency_key, решили передавать через консоль, тк в консольном прототипе нет заголовков
    if len(sys.argv) < 3:
        print("Как использовать:")
        print("  Создать: python 05_app.py create <файл.json> <idempotency_key>")
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
        with open(sys.argv[2], encoding="utf-8") as f:
            payload = json.load(f)
        payment_id = "pay_demo_" + payload["order_id"][-4:]

        # v-9 теперь тут
        with open(OUT_CSV, 'r', encoding="utf-8-sig") as f:

            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('idempotency_key') == idem_key:
                    print('Это повторный запрос. Возвращаем результат предыдущего платежа:')
                    print(json.dumps({
                        "payment_id": payment_id,
                        "status": row.get('status'),
                        "confirm_token": uuid.uuid4().hex[:6],
                        'expires_in': 540
                    }, ensure_ascii=False, indent=2))
                    sys.exit(0)
                    break


        active = load_active_recipients()
        errors = validate(payload, active, idem_key)


        if errors:
            has_DUPLICATE_ORDER = any([True if 'DUPLICATE_ORDER' in er else False for er in errors])
            if has_DUPLICATE_ORDER:
                print(json.dumps({
                    "error_code": "DUPLICATE_ORDER",
                    'message': "Заказ с таким идентификатором уже оплачен или обрабатывается",
                    'retryable': False
                }, ensure_ascii=False, indent=2))
                sys.exit(409)
            else:
                print(json.dumps({
                    "error_code": "VALIDATION_ERROR",
                    "message": "Ошибка валидации данных",
                    'retryable': False,
                    "errors": [{"field": f, "code": c} for f, c in errors],
                }, ensure_ascii=False, indent=2))
                write_result(payload, 'ERROR', 'VALIDATION_ERROR', idem_key)
                sys.exit(422)

        status = 'CREATED'
        print(f'Платеж создан {payload.get("order_id")}, статус {status}')
        writeLog(payment_id, status)
        time.sleep(1)
        confirm_token = uuid.uuid4().hex[:6]
        print(f'Токен сгенерировал: {confirm_token}, оправлен пользователю, ждем подтверждения \n')
        print('Ожидание подтверждения клиентом ', end='\n')
        show_loading(5)

        status = 'PENDING'
        print(f'Токен подтвержден пользователем, статус {status}')
        writeLog(payment_id,status )
        time.sleep(1)
        print()
        print('Запрос отправлен в банк')
        show_loading(2)

        gateway_status, error_code = emulate_gateway(payload["order_id"])
        status = "PENDING" if gateway_status == "TIMEOUT" else gateway_status

        writeLog(payment_id, status)
        write_result(payload, status, error_code, idem_key)

        print('Результат:')
        print(json.dumps({
            "payment_id": payment_id,
            "status": status,
            'confirm_token': confirm_token,
            "expires_in": 600,
            "error_code": error_code,
        }, ensure_ascii=False, indent=2))

        sys.exit(0 if status == "SUCCESS" else (3 if error_code == "UNKNOWN" else 1))
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
            print('Платёж с указанным идентификатором не найден')
            print(json.dumps({
                'error_code': "PAYMENT_NOT_FOUND",
                'message': "Платёж с ID pay_9999 не найден в базе данных клиники",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(400)
    elif action == 'cancel':
        payment_id = sys.argv[2]
        founded_row = False
        updated_rows = []
        fieldnames = [
            "payment_id", "order_id", "recipient", "purpose", "amount",
            "currency", "status", "error_code", "idempotency_key",
            "created_at", "confirmed_at", "correlation_id"
        ]
        with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('payment_id') == payment_id:
                    if row['status'] in ['CREATED', 'PENDING']:
                        row['status'] = 'CANCELED'
                        founded_row = row
                        writeLog(payment_id, 'CANCELED')
                updated_rows.append(row)
        if founded_row:
            history = getLogsHistory(payment_id)
            with open(OUT_CSV, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, delimiter=';', fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            print(json.dumps({
                "payment_id": payment_id,
                "status": "CANCELED",
                "error_code": None,
                "amount": float(founded_row.get("amount")),
                "currency": founded_row.get("currency"),
                "history": history
            }, ensure_ascii=False, indent=2))
        else:
            print("Платеж не найден или его нельзя отменить (не статус CREATED/PENDING)")
            print(json.dumps({
                'error_code': "INVALID_PAYMENT_STATE",
                'message': "Нельзя отменить уже оплаченный платёж",
                'retryable': False
            }, ensure_ascii=False, indent=2))
            sys.exit(409)





if __name__ == "__main__":
    main()
