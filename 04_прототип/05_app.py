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
import datetime
import json
import os
import re
import sys
import time
import uuid
from decimal import Decimal, InvalidOperation
from gateway_emulator import emulate_gateway

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPIENTS_CSV = os.path.join(BASE_DIR, "01_данные", "recipients.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")
EVENTS_LOG = os.path.join(BASE_DIR, "events.log")
TOKENS = os.path.join(BASE_DIR, "tokens.json")

TOKEN_TTL_SECONDS = 600
MSK_TZ = datetime.timezone(datetime.timedelta(hours=3))

fieldnames = [
    "payment_id", "order_id", "recipient", "purpose", "amount",
    "currency", "status", "error_code", "idempotency_key",
    "created_at", "confirmed_at", "correlation_id"
]


def now_iso():
    """Текущее время в формате ISO-8601 (МСК)."""
    return datetime.datetime.now(MSK_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def load_tokens():
    """Безопасное чтение tokens.json."""
    if os.path.exists(TOKENS):
        try:
            with open(TOKENS, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_tokens(tokens_data):
    """Сохранение tokens.json."""
    with open(TOKENS, "w", encoding="utf-8") as f:
        json.dump(tokens_data, f, ensure_ascii=False, indent=2)


def is_empty(val):
    """Проверка на None, пустую строку или строку из одних пробелов."""
    if val is None:
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


def validate(payload, active_recipients, idem_key):
    """Реализовать V-1...V-8. Вернуть [(field, code), ...]."""
    errors = []

    # V-1: Обязательные поля
    for field in ["order_id", "recipient", "purpose", "amount", "currency"]:
        if is_empty(payload.get(field)):
            errors.append((field, "VALIDATION_ERROR"))

    # V-2: Формат order_id (строка 1..64 символов A-Za-z0-9_-)
    order_id_val = payload.get("order_id")
    if not is_empty(order_id_val):
        if not isinstance(order_id_val, str):
            errors.append(("order_id", "INVALID_FORMAT"))
        else:
            pattern = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
            if not pattern.match(order_id_val):
                errors.append(("order_id", "INVALID_FORMAT"))

    # V-3 и V-7: Проверка типа, диапазона и точности суммы (amount)
    raw_amount = payload.get("amount")
    if not is_empty(raw_amount):
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
            errors.append(("amount", "INVALID_FORMAT"))
        else:
            try:
                dec_amt = Decimal(str(raw_amount))
                if not dec_amt.is_finite():
                    errors.append(("amount", "INVALID_FORMAT"))
                else:
                    # V-3: Диапазон суммы (0 < amount <= 1_000_000)
                    if dec_amt <= Decimal("0") or dec_amt > Decimal("1000000"):
                        errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))
                    # V-7: Не более 2 знаков после запятой
                    if dec_amt.as_tuple().exponent < -2:
                        errors.append(("amount", "INVALID_AMOUNT_PRECISION"))
            except (InvalidOperation, ValueError, TypeError):
                errors.append(("amount", "INVALID_FORMAT"))

    # V-4: Валюта (только RUB)
    currency = payload.get("currency")
    if not is_empty(currency) and currency != "RUB":
        errors.append(("currency", "UNSUPPORTED_CURRENCY"))

    # V-5: Активный получатель
    recipient = payload.get("recipient")
    if not is_empty(recipient) and recipient not in active_recipients:
        errors.append(("recipient", "RECIPIENT_NOT_FOUND"))

    # V-6: Назначение платежа (длина до 200 символов и отсутствие PAN 13-19 цифр, даже с пробелами/дефисами)
    purpose = payload.get("purpose")
    if not is_empty(purpose):
        if not isinstance(purpose, str):
            errors.append(("purpose", "INVALID_PURPOSE"))
        else:
            pattern_pan = re.compile(r"(?:\d[\s\-]?){12,18}\d")
            if len(purpose) > 200 or pattern_pan.search(purpose):
                errors.append(("purpose", "INVALID_PURPOSE"))

    # V-8: Дубликат заказа у того же получателя
    if not is_empty(order_id_val) and not is_empty(recipient) and os.path.exists(OUT_CSV):
        with open(OUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("order_id") == str(order_id_val) and row.get("recipient") == str(recipient):
                    errors.append(("order_id", "DUPLICATE_ORDER"))
                    break

    return errors


def init_out_csv():
    if not os.path.exists(OUT_CSV):
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(fieldnames)


def write_result(payload, status, error_code, idem_key, payment_id):
    time_now = now_iso()
    corr_id = "corr_" + uuid.uuid4().hex[:6]
    with open(OUT_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
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
            time_now if status == "SUCCESS" else "",
            corr_id,
        ])


def writeLog(payment_id, status):
    time_str = datetime.datetime.now(MSK_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(EVENTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time_str} Платеж {payment_id}: {status}\n")


def getLogsHistory(payment_id):
    history = []
    if not os.path.exists(EVENTS_LOG):
        return history

    with open(EVENTS_LOG, "r", encoding="utf-8-sig") as f:
        for l in f:
            if payment_id in l:
                data = l.split(" ")
                if len(data) >= 2:
                    time_str = data[0] + "T" + data[1] + "Z"
                    status = data[-1].strip()
                    history.append({"status": status, "at": time_str})
    return history


def main():
    init_out_csv()
    if len(sys.argv) < 3:
        print("Как использовать:")
        print("  Создать:     python 05_app.py create <файл.json> <idempotency_key>")
        print("  Подтвердить: python 05_app.py confirm <payment_id> <confirm_token>")
        print("  Статус:      python 05_app.py status <payment_id>")
        print("  Отмена:      python 05_app.py cancel <payment_id>")
        sys.exit(1)

    action = sys.argv[1]

    if action == "create":
        if len(sys.argv) < 4:
            print("Некорректный запрос. Отсутствуют данные")
            print(json.dumps({
                "error_code": "BAD_REQUEST",
                "message": "Не удалось распарсить тело запроса: ожидался корректный JSON и ключ идемпотентности",
                "retryable": False
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

        idem_key = sys.argv[3]

        try:
            with open(sys.argv[2], encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError
        except FileNotFoundError:
            print(json.dumps({"error_code": "BAD_REQUEST", "message": "Файл запроса не найден"}, ensure_ascii=False, indent=2))
            sys.exit(400)
        except (json.JSONDecodeError, ValueError):
            print(json.dumps({"error_code": "BAD_REQUEST", "message": "Невалидный JSON"}, ensure_ascii=False, indent=2))
            sys.exit(400)

        # V-9: Проверка идемпотентности create
        with open(OUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("idempotency_key") == idem_key:
                    existing_pid = row.get("payment_id")
                    existing_status = row.get("status")
                    tokens_data = load_tokens()
                    token_info = tokens_data.get(existing_pid)

                    if token_info and existing_status == "CREATED":
                        elapsed = int(time.time() - token_info.get("created_at", time.time()))
                        expires_in = max(0, TOKEN_TTL_SECONDS - elapsed)
                        saved_token = token_info.get("token")
                    else:
                        expires_in = 0
                        saved_token = None

                    print("Это повторный запрос. Возвращаем результат предыдущего платежа:")
                    print(json.dumps({
                        "payment_id": existing_pid,
                        "status": existing_status,
                        "confirm_token": saved_token,
                        "expires_in": expires_in,
                        "error_code": "DUPLICATE_REQUEST"
                    }, ensure_ascii=False, indent=2))
                    sys.exit(0)

        active = load_active_recipients()
        errors = validate(payload, active, idem_key)

        if errors:
            has_DUPLICATE_ORDER = any("DUPLICATE_ORDER" in er for er in errors)
            has_RECIPIENT_NOT_FOUND = any("RECIPIENT_NOT_FOUND" in er for er in errors)
            if has_DUPLICATE_ORDER:
                print(json.dumps({
                    "error_code": "DUPLICATE_ORDER",
                    "message": "Заказ с таким идентификатором уже оплачен или обрабатывается",
                    "retryable": False
                }, ensure_ascii=False, indent=2))
                sys.exit(409)
            elif has_RECIPIENT_NOT_FOUND:
                print(json.dumps({
                    "error_code": "RECIPIENT_NOT_FOUND",
                    "message": "Указанный получатель платежа не зарегистрирован в системе",
                    "retryable": False
                }, ensure_ascii=False, indent=2))
                sys.exit(404)
            else:
                print(json.dumps({
                    "error_code": "VALIDATION_ERROR",
                    "message": "Ошибка валидации данных",
                    "retryable": False,
                    "errors": [{"field": f, "code": c} for f, c in errors],
                }, ensure_ascii=False, indent=2))
                sys.exit(422)

        payment_id = "pay_demo_" + uuid.uuid4().hex[:6]
        status = "CREATED"
        print(f'Платеж создан {payload.get("order_id")}, статус {status}')
        writeLog(payment_id, status)

        confirm_token = uuid.uuid4().hex[:6]
        print(f"Токен сгенерирован: {confirm_token}, отправлен пользователю, ждем подтверждения\n")

        tokens_data = load_tokens()
        tokens_data[payment_id] = {
            "token": confirm_token,
            "attempts": 0,
            "created_at": time.time()
        }
        save_tokens(tokens_data)

        write_result(payload, "CREATED", "", idem_key, payment_id)
        print(json.dumps({
            "payment_id": payment_id,
            "status": status,
            "confirm_token": confirm_token,
            "expires_in": TOKEN_TTL_SECONDS,
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    elif action == "confirm":
        if len(sys.argv) < 4:
            print("Некорректный запрос. Отсутствуют данные")
            sys.exit(1)
        payment_id = sys.argv[2]
        user_token = sys.argv[3]

        found_row = None
        updated_rows = []
        with open(OUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("payment_id") == payment_id:
                    found_row = row
                updated_rows.append(row)

        if not found_row:
            print(json.dumps({
                "error_code": "PAYMENT_NOT_FOUND",
                "message": "Платеж не найден"
            }, ensure_ascii=False, indent=2))
            sys.exit(404)

        tokens_data = load_tokens()

        # Если активного токена уже нет в tokens.json
        if payment_id not in tokens_data:
            current_status = found_row.get("status")
            saved_error = found_row.get("error_code")
            history = getLogsHistory(payment_id)

            # 1. Если платеж был отменен
            if current_status == "CANCELED":
                print(json.dumps({
                    "payment_id": payment_id,
                    "status": "CANCELED",
                    "error_code": "INVALID_STATE",
                    "message": "Платеж был отменен и не может быть подтвержден",
                    "retryable": False
                }, ensure_ascii=False, indent=2))
                sys.exit(409)

            # 2. Если токен ранее истек по TTL
            if saved_error == "TOKEN_EXPIRED":
                print(json.dumps({
                    "payment_id": payment_id,
                    "status": "DECLINED",
                    "error_code": "TOKEN_EXPIRED",
                    "message": "Время действия токена истекло"
                }, ensure_ascii=False, indent=2))
                sys.exit(410)

            # 3. Если ранее был превышен лимит из 3 попыток ввода токена
            if saved_error == "INVALID_CONFIRM_TOKEN":
                print(json.dumps({
                    "payment_id": payment_id,
                    "status": "DECLINED",
                    "error_code": "INVALID_CONFIRM_TOKEN",
                    "message": "Превышено количество попыток ввода токена"
                }, ensure_ascii=False, indent=2))
                sys.exit(410)

            # 4. Идемпотентный ответ при повторном confirm после вызова шлюза (SUCCESS / PENDING / DECLINED от банка)
            print("Повторный запрос confirm. Шлюз повторно не вызывался:")
            print(json.dumps({
                "payment_id": payment_id,
                "status": current_status,
                "error_code": saved_error if saved_error else "DUPLICATE_REQUEST",
                "amount": float(found_row["amount"]),
                "currency": found_row["currency"],
                "message": "Платеж уже был обработан ранее",
                "history": history
            }, ensure_ascii=False, indent=2))
            sys.exit(0)

        token_info = tokens_data[payment_id]

        # Проверка истечения TTL токена (600 сек)
        if time.time() - token_info["created_at"] > TOKEN_TTL_SECONDS:
            print(json.dumps({
                "error_code": "TOKEN_EXPIRED",
                "message": "Время действия токена истекло"
            }, ensure_ascii=False, indent=2))

            for row in updated_rows:
                if row.get("payment_id") == payment_id:
                    row["status"] = "DECLINED"
                    row["error_code"] = "TOKEN_EXPIRED"

            with open(OUT_CSV, mode="w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, delimiter=";", fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)

            writeLog(payment_id, "DECLINED")

            del tokens_data[payment_id]
            save_tokens(tokens_data)
            sys.exit(410)

        # Проверка совпадения токена и лимита попыток (до 3 попыток)
        if user_token != token_info["token"]:
            token_info["attempts"] += 1

            if token_info.get("attempts") >= 3:
                print(json.dumps({
                    "error_code": "INVALID_CONFIRM_TOKEN",
                    "message": "Превышено количество попыток"
                }, ensure_ascii=False, indent=2))

                del tokens_data[payment_id]
                save_tokens(tokens_data)

                for row in updated_rows:
                    if row.get("payment_id") == payment_id:
                        row["status"] = "DECLINED"
                        row["error_code"] = "INVALID_CONFIRM_TOKEN"

                writeLog(payment_id, "DECLINED")

                with open(OUT_CSV, mode="w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, delimiter=";", fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(updated_rows)

                sys.exit(410)
            else:
                print(json.dumps({
                    "error_code": "INVALID_CONFIRM_TOKEN",
                    "message": f"Неверный код. Осталось попыток: {3 - token_info['attempts']}"
                }, ensure_ascii=False, indent=2))
                save_tokens(tokens_data)
                sys.exit(422)

        # Токен верный — удаляем одноразовый токен и идем в шлюз ровно 1 раз
        del tokens_data[payment_id]
        save_tokens(tokens_data)

        order_id = found_row.get("order_id")
        print("Токен верный. Запрос отправлен в банк...")
        gateway_status, error_code = emulate_gateway(order_id)
        status = "PENDING" if gateway_status == "TIMEOUT" else gateway_status

        time_now = now_iso()

        for row in updated_rows:
            if row["payment_id"] == payment_id:
                row["status"] = status
                row["error_code"] = error_code or ""
                row["confirmed_at"] = time_now if status == "SUCCESS" else ""

        with open(OUT_CSV, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, delimiter=";", fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(updated_rows)

        writeLog(payment_id, status)
        history = getLogsHistory(payment_id)
        print("Результат подтверждения:")
        print(json.dumps({
            "payment_id": payment_id,
            "status": status,
            "error_code": error_code,
            "amount": float(found_row["amount"]),
            "currency": found_row["currency"],
            "history": history
        }, ensure_ascii=False, indent=2))

        if status == "SUCCESS":
            sys.exit(0)
        elif error_code in ("TIMEOUT", "UNKNOWN"):
            sys.exit(504)
        else:
            sys.exit(402)

    elif action == "status":
        payment_id = sys.argv[2]
        found_row = None
        with open(OUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("payment_id") == payment_id:
                    found_row = row
                    break

        if found_row:
            history = getLogsHistory(payment_id)
            print("Информация о платеже успешно получена")
            error_code = found_row.get("error_code")
            print(json.dumps({
                "payment_id": found_row.get("payment_id"),
                "status": found_row.get("status"),
                "error_code": error_code if error_code else None,
                "amount": float(found_row.get("amount")),
                "currency": found_row.get("currency"),
                "history": history
            }, ensure_ascii=False, indent=2))
            sys.exit(0)
        else:
            print(json.dumps({
                "error_code": "PAYMENT_NOT_FOUND",
                "message": f"Платёж с ID {payment_id} не найден в базе данных клиники",
                "retryable": False
            }, ensure_ascii=False, indent=2))
            sys.exit(404)

    elif action == "cancel":
        payment_id = sys.argv[2]
        payment_exists = False
        canceled = False
        is_pending = False
        updated_rows = []
        founded_row = None

        with open(OUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("payment_id") == payment_id:
                    payment_exists = True
                    # Отменять можно ТОЛЬКО платеж в статусе CREATED (пока запрос не ушел в банк)
                    if row["status"] == "CREATED":
                        row["status"] = "CANCELED"
                        canceled = True
                        founded_row = row
                        writeLog(payment_id, "CANCELED")
                    elif row["status"] == "PENDING":
                        is_pending = True
                updated_rows.append(row)

        if canceled:
            history = getLogsHistory(payment_id)
            with open(OUT_CSV, mode="w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, delimiter=";", fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)

            tokens_data = load_tokens()
            if payment_id in tokens_data:
                del tokens_data[payment_id]
                save_tokens(tokens_data)

            print(json.dumps({
                "payment_id": payment_id,
                "status": "CANCELED",
                "error_code": None,
                "amount": float(founded_row.get("amount")),
                "currency": founded_row.get("currency"),
                "history": history
            }, ensure_ascii=False, indent=2))
            sys.exit(0)

        elif is_pending:
            print(json.dumps({
                "error_code": "INVALID_STATE",
                "message": "Платёж находится в обработке у банка (PENDING). Отмена запрещена до сверки финального статуса, чтобы исключить списание средств без учёта оплаты",
                "retryable": True
            }, ensure_ascii=False, indent=2))
            sys.exit(409)

        elif payment_exists:
            print(json.dumps({
                "error_code": "INVALID_STATE",
                "message": "Нельзя отменить уже оплаченный, отклоненный или отмененный платёж",
                "retryable": False
            }, ensure_ascii=False, indent=2))
            sys.exit(409)

        else:
            print(json.dumps({
                "error_code": "PAYMENT_NOT_FOUND",
                "message": f"Платёж с ID {payment_id} не найден в базе данных",
                "retryable": False
            }, ensure_ascii=False, indent=2))
            sys.exit(404)

    else:
        print(f"Неизвестная команда: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()