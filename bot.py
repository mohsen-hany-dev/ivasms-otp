import argparse
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
COUNTRY_FILE = BASE_DIR / "country_codes.json"
PLATFORMS_FILE = BASE_DIR / "platforms.json"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
GROUPS_FILE = BASE_DIR / "groups.json"
STORE_FILE = BASE_DIR / "sent_codes_store.json"
TOKEN_CACHE_FILE = BASE_DIR / "token_cache.json"
SETTINGS_FILE = BASE_DIR / "runtime_config.json"
DAILY_STORE_DIR = BASE_DIR / "daily_messages"
TOKEN_TTL_SECONDS = 2 * 60 * 60
TOKEN_REFRESH_SKEW_SECONDS = 5 * 60


def ask(prompt: str, default: str | None = None) -> str:
    if default is None:
        return input(f"{prompt}: ").strip()
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def ask_missing(prompt: str, current: str) -> str:
    if current.strip():
        return current.strip()
    return ask(prompt)


def digits_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def load_countries() -> list[dict[str, str]]:
    rows = [x for x in load_json_list(COUNTRY_FILE) if x.get("dial_code")]
    rows.sort(key=lambda x: len(str(x.get("dial_code", ""))), reverse=True)
    return rows


def load_platforms() -> dict[str, str]:
    rows = load_json_list(PLATFORMS_FILE)
    out: dict[str, str] = {}
    for r in rows:
        key = str(r.get("key", "")).strip().lower()
        short = str(r.get("short", "")).strip()
        if key and short:
            out[key] = short
    return out


def load_accounts() -> list[dict[str, str]]:
    rows = load_json_list(ACCOUNTS_FILE)
    # Backward compatible loader: supports JSON object {"accounts":[...]}
    # and simple line format: "email password".
    if not rows and ACCOUNTS_FILE.exists():
        try:
            raw = ACCOUNTS_FILE.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                obj = json.loads(raw)
                maybe_rows = obj.get("accounts") if isinstance(obj, dict) else None
                if isinstance(maybe_rows, list):
                    rows = [x for x in maybe_rows if isinstance(x, dict)]
            elif raw:
                parsed_rows: list[dict[str, str]] = []
                for idx, line in enumerate(raw.splitlines(), start=1):
                    v = line.strip()
                    if not v or v.startswith("#"):
                        continue
                    parts = v.split()
                    if len(parts) >= 2:
                        email = parts[0].strip()
                        password = " ".join(parts[1:]).strip()
                        parsed_rows.append(
                            {
                                "name": f"account_{idx}",
                                "email": email,
                                "password": password,
                                "enabled": True,
                            }
                        )
                rows = parsed_rows
        except Exception:
            rows = []
    out: list[dict[str, str]] = []
    for r in rows:
        enabled = bool(r.get("enabled", True))
        email = str(r.get("email", "")).strip()
        password = str(r.get("password", "")).strip()
        name = str(r.get("name", email)).strip() or email
        if enabled and email and password:
            out.append({"name": name, "email": email, "password": password})
    return out


def load_groups() -> list[dict[str, str]]:
    rows = load_json_list(GROUPS_FILE)
    out: list[dict[str, str]] = []
    for r in rows:
        enabled = bool(r.get("enabled", True))
        chat_id = str(r.get("chat_id", "")).strip()
        name = str(r.get("name", chat_id)).strip() or chat_id
        if enabled and chat_id:
            out.append({"name": name, "chat_id": chat_id})
    return out


def detect_country(number: str, countries: list[dict[str, str]]) -> dict[str, str]:
    num = digits_only(number)
    if num.startswith("00"):
        num = num[2:]
    for row in countries:
        dial = str(row.get("dial_code", ""))
        if dial and num.startswith(dial):
            return row
    return {"name_ar": "غير معروف", "name_en": "Unknown", "iso2": "UN", "dial_code": ""}


def iso_to_flag(iso2: str) -> str:
    code = (iso2 or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳️"
    base = 127397
    return chr(base + ord(code[0])) + chr(base + ord(code[1]))


def service_short(service_name: str, platforms: dict[str, str]) -> str:
    key = (service_name or "").strip().lower()
    if key in platforms:
        return str(platforms[key]).upper()
    return (service_name[:2] or "NA").upper()


def service_emoji_id(service_name: str, platform_rows: list[dict]) -> str:
    key = (service_name or "").strip().lower()
    for row in platform_rows:
        if str(row.get("key", "")).strip().lower() == key:
            return str(row.get("emoji_id", "")).strip()
    return ""


def service_emoji_alt(service_name: str, platform_rows: list[dict]) -> str:
    key = (service_name or "").strip().lower()
    for row in platform_rows:
        if str(row.get("key", "")).strip().lower() == key:
            alt = str(row.get("emoji", "")).strip()
            if alt:
                return alt
    return "✨"


def extract_code(message: str) -> str:
    text = message or ""
    # Prefer patterns like 123-456 then fallback to plain 4-8 digits.
    m = re.search(r"\b\d{2,4}-\d{2,4}\b", text)
    if m:
        return m.group(0)
    m2 = re.search(r"\b\d{4,8}\b", text)
    if m2:
        return m2.group(0)
    return ""


def build_message(item: dict, countries: list[dict[str, str]], platforms: dict[str, str], platform_rows: list[dict]) -> str:
    raw_number = str(item.get("number", ""))
    number_digits = digits_only(raw_number)
    number_with_plus = f"+{number_digits}" if number_digits else raw_number
    service_name = str(item.get("service_name", "Unknown"))
    short = service_short(service_name, platforms)
    semoji_id = service_emoji_id(service_name, platform_rows)
    semoji_alt = service_emoji_alt(service_name, platform_rows)
    use_custom_emoji = os.getenv("USE_CUSTOM_EMOJI", "0").strip() == "1"
    country = detect_country(raw_number, countries)
    iso2 = country.get("iso2", "UN")
    flag = iso_to_flag(iso2)
    message_text = str(item.get("message", "")).strip()
    escaped_head = _md_escape(f"{short} {iso2} {flag} {number_with_plus}")
    escaped_msg = _md_code_escape(message_text)
    custom = f"![{semoji_alt}](tg://emoji?id={semoji_id}) " if (use_custom_emoji and semoji_id) else f"{semoji_alt} "
    return f"> {custom}*{escaped_head}*\n```\n{escaped_msg}\n```"


def _md_escape(text: str) -> str:
    # MarkdownV2 special chars
    out = re.sub(r"([_\\*\\[\\]\\(\\)~`>#+\\-=|{}.!])", r"\\\1", text or "")
    return out.replace("+", r"\+")


def _md_code_escape(text: str) -> str:
    t = text or ""
    # Keep code block valid.
    t = t.replace("```", "'''")
    return t


def send_telegram_message(bot_token: str, chat_id: str, text: str, copy_value: str) -> dict:
    api = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": f"{copy_value}", "style": "success", "copy_text": {"text": copy_value}}],
            ]
        },
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=30)
    data = r.json()
    if data.get("ok"):
        return data

    # Fallback if copy_text is unsupported in the current Bot API/client environment.
    payload["reply_markup"] = {
        "inline_keyboard": [
            [{"text": f"{copy_value}", "style": "success", "url": f"https://t.me/share/url?url={copy_value}"}],
        ]
    }
    r2 = requests.post(api, json=payload, timeout=30)
    return r2.json()


def _today_key() -> str:
    return date.today().isoformat()


def _daily_store_path(day_key: str) -> Path:
    return DAILY_STORE_DIR / f"messages_{day_key}.json"


def cleanup_old_daily_files(current_day_key: str) -> None:
    DAILY_STORE_DIR.mkdir(parents=True, exist_ok=True)
    keep_path = _daily_store_path(current_day_key).resolve()
    for p in DAILY_STORE_DIR.glob("messages_*.json"):
        try:
            if p.resolve() != keep_path:
                p.unlink(missing_ok=True)
        except Exception:
            continue


def load_daily_store(day_key: str) -> dict:
    DAILY_STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _daily_store_path(day_key)
    if not path.exists():
        return {"day": day_key, "seen_keys": [], "sent": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen_keys"), list) and isinstance(data.get("sent"), list):
            data["day"] = day_key
            return data
    except Exception:
        pass
    return {"day": day_key, "seen_keys": [], "sent": []}


def save_daily_store(day_key: str, store: dict) -> None:
    DAILY_STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _daily_store_path(day_key)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def load_token_cache() -> dict:
    if not TOKEN_CACHE_FILE.exists():
        return {"accounts": {}}
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
            return data
    except Exception:
        pass
    return {"accounts": {}}


def save_token_cache(cache: dict) -> None:
    TOKEN_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_get_valid_token(cache: dict, account_name: str) -> str | None:
    row = (cache.get("accounts") or {}).get(account_name)
    if not isinstance(row, dict):
        return None
    token = str(row.get("token", "")).strip()
    expires_at = int(row.get("expires_at", 0) or 0)
    if not token or expires_at <= int(time.time()) + TOKEN_REFRESH_SKEW_SECONDS:
        return None
    return token


def cache_set_token(cache: dict, account_name: str, token: str) -> None:
    now = int(time.time())
    cache.setdefault("accounts", {})[account_name] = {
        "token": token,
        "obtained_at": now,
        "expires_at": now + TOKEN_TTL_SECONDS,
    }


def get_or_refresh_account_token(
    api_base: str,
    account: dict[str, str],
    account_tokens: dict[str, str],
    token_cache: dict,
) -> str | None:
    name = account["name"]
    mem_tok = account_tokens.get(name)
    if mem_tok and cache_get_valid_token(token_cache, name):
        return mem_tok

    cached_tok = cache_get_valid_token(token_cache, name)
    if cached_tok:
        account_tokens[name] = cached_tok
        return cached_tok

    new_tok = api_login(api_base, account["email"], account["password"])
    if not new_tok:
        return None
    account_tokens[name] = new_tok
    cache_set_token(token_cache, name, new_tok)
    save_token_cache(token_cache)
    return new_tok


def msg_key(item: dict) -> str:
    number = str(item.get("number", ""))
    service_name = str(item.get("service_name", ""))
    message = str(item.get("message", ""))
    rng = str(item.get("range", ""))
    return f"{number}|{service_name}|{rng}|{message}"


def normalize_start_date(raw: str) -> str:
    v = (raw or "").strip()
    parts = v.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        if len(y) == 4:
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return date.today().isoformat()


def load_settings() -> dict[str, str]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v).strip() for k, v in data.items() if v is not None}
    except Exception:
        return {}


def save_settings(settings: dict[str, str]) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def api_login(api_base: str, email: str, password: str) -> str | None:
    try:
        r = requests.post(
            f"{api_base}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=90,
        )
        j = r.json()
        if r.status_code == 200:
            return (j.get("data") or {}).get("token")
    except Exception:
        return None
    return None


def fetch_messages(api_base: str, api_token: str, start_date: str, limit: int) -> list[dict]:
    r = requests.post(
        f"{api_base}/api/v1/biring/code",
        json={"token": api_token, "start_date": start_date},
        timeout=600,
    )
    j = r.json()
    if r.status_code != 200:
        raise RuntimeError(str(j))
    return ((j.get("data") or {}).get("messages") or [])[:limit]


def run_loop(start_date: str, api_base: str, api_token: str, tg_token: str, target_groups: list[dict[str, str]], limit: int, once: bool) -> None:
    countries = load_countries()
    platform_rows = load_json_list(PLATFORMS_FILE)
    platforms = load_platforms()
    active_day = _today_key()
    cleanup_old_daily_files(active_day)
    day_store = load_daily_store(active_day)
    seen_keys = set(day_store.get("seen_keys", []))

    accounts = load_accounts()
    token_cache = load_token_cache()
    account_tokens: dict[str, str] = {}
    for acc in accounts:
        tok = get_or_refresh_account_token(api_base, acc, account_tokens, token_cache)
        if tok:
            print(f"account ready: {acc['name']}")
        else:
            print(f"account login failed: {acc['name']}")

    print(f"\nStarted polling every 30 seconds | start_date={start_date} | limit={limit}")
    print("Press Ctrl+C to stop.\n")

    while True:
        now_day = _today_key()
        if now_day != active_day:
            active_day = now_day
            cleanup_old_daily_files(active_day)
            day_store = load_daily_store(active_day)
            seen_keys = set(day_store.get("seen_keys", []))
            print(f"Rotated daily message store to {active_day}")

        all_rows: list[dict] = []

        if api_token:
            try:
                all_rows.extend(fetch_messages(api_base, api_token, start_date, limit))
            except Exception as exc:
                print(f"API token fetch failed: {exc}")

        for acc in accounts:
            name = acc["name"]
            tok = get_or_refresh_account_token(api_base, acc, account_tokens, token_cache)
            if not tok:
                continue
            try:
                all_rows.extend(fetch_messages(api_base, tok, start_date, limit))
            except Exception:
                new_tok = api_login(api_base, acc["email"], acc["password"])
                if not new_tok:
                    continue
                account_tokens[name] = new_tok
                cache_set_token(token_cache, name, new_tok)
                save_token_cache(token_cache)
                try:
                    all_rows.extend(fetch_messages(api_base, new_tok, start_date, limit))
                except Exception:
                    continue

        uniq: dict[str, dict] = {}
        for row in all_rows:
            uniq[msg_key(row)] = row
        rows = list(uniq.values())[:limit]
        new_rows = [x for x in rows if msg_key(x) not in seen_keys]

        if not new_rows:
            print(f"[{time.strftime('%H:%M:%S')}] no new messages")
            if once:
                return
            time.sleep(30)
            continue

        print(f"[{time.strftime('%H:%M:%S')}] new messages: {len(new_rows)}")
        for idx, item in enumerate(new_rows, start=1):
            number = str(item.get("number", ""))
            message_text = str(item.get("message", ""))
            code = extract_code(message_text) or number
            text = build_message(item, countries, platforms, platform_rows)

            any_sent = False
            sent_info: list[dict[str, str | int | None]] = []
            for grp in target_groups:
                gid = grp["chat_id"]
                gname = grp["name"]
                try:
                    j = send_telegram_message(tg_token, gid, text, code)
                except Exception as exc:
                    print(f"[{idx}] send failed ({gname}): {exc}")
                    continue
                if not j.get("ok"):
                    print(f"[{idx}] send failed ({gname}): {j}")
                    continue
                any_sent = True
                msg_id = (j.get("result") or {}).get("message_id")
                sent_info.append({"group": gname, "chat_id": gid, "message_id": msg_id})
                print(f"[{idx}] sent -> {gname} | message_id={msg_id} | code={code}")

            if any_sent:
                mkey = msg_key(item)
                seen_keys.add(mkey)
                day_store["sent"].append(
                    {
                        "number": number,
                        "code": code,
                        "service_name": item.get("service_name"),
                        "range": item.get("range"),
                        "message": item.get("message"),
                        "revenue": item.get("revenue"),
                        "groups": sent_info,
                        "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                day_store["seen_keys"] = list(seen_keys)
                save_daily_store(active_day, day_store)

        if once:
            return
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(description="NumPlus Telegram Bot Client")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle then exit")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    persisted = load_settings()

    default_api = (persisted.get("API_BASE_URL") or os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).strip()
    default_start = (persisted.get("API_START_DATE") or os.getenv("API_START_DATE", "2025-01-01")).strip()
    default_api_token = (persisted.get("API_SESSION_TOKEN") or os.getenv("API_SESSION_TOKEN", "")).strip()
    default_tg_token = (persisted.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    default_chat_id = (persisted.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    default_limit = (persisted.get("BOT_LIMIT") or os.getenv("BOT_LIMIT", "30")).strip()

    print("=== NumPlus Telegram Bot Client ===")
    api_base = ask_missing("API domain", default_api).rstrip("/")
    tg_token = ask_missing("Telegram bot token", default_tg_token)

    groups = load_groups()
    if groups:
        target_groups = groups
    else:
        chat_id = ask_missing("Telegram group/chat id", default_chat_id)
        target_groups = [{"name": "default_group", "chat_id": chat_id}]

    # Ask only if token missing and no usable accounts file.
    accounts = load_accounts()
    api_token = default_api_token
    if not api_token and not accounts:
        api_token = ask("API session token (missing and no accounts found)")

    # Keep start date interactive every run, while other core settings stay persisted.
    start_date_raw = ask("Start date YYYY-MM-DD", default_start or date.today().isoformat())
    start_date = normalize_start_date(start_date_raw)
    if start_date != start_date_raw:
        print(f"Normalized/invalid date input. Using: {start_date}")

    limit_raw = ask("Messages limit", default_limit or "30")
    try:
        limit = max(1, min(100, int(limit_raw)))
    except Exception:
        limit = 30

    # Persist effective runtime values to avoid repeated prompts.
    saved_chat_id = default_chat_id
    if target_groups:
        saved_chat_id = str(target_groups[0].get("chat_id", "")).strip() or default_chat_id
    save_settings(
        {
            "API_BASE_URL": api_base,
            "API_START_DATE": start_date,
            "API_SESSION_TOKEN": api_token,
            "TELEGRAM_BOT_TOKEN": tg_token,
            "TELEGRAM_CHAT_ID": saved_chat_id,
            "BOT_LIMIT": str(limit),
        }
    )

    try:
        run_loop(start_date, api_base, api_token, tg_token, target_groups, limit, args.once)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
