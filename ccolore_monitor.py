"""
꼴로르 에어라이트 400 - 전 색상 L 사이즈 재고 알림
=====================================================

동작: 4개 색상 상품 페이지를 주기적으로 확인해서 L 옵션의 [품절]이 풀리면
      텔레그램(또는 ntfy)으로 즉시 알림을 보냅니다.

실행:
  python ccolore_monitor.py            # 계속 돌면서 감시 (PC용, 기본 60초 간격)
  python ccolore_monitor.py --once     # 한 번만 확인하고 종료 (GitHub Actions용)
  python ccolore_monitor.py --test     # 알림이 잘 오는지 테스트 메시지 전송

알림 설정 (환경변수 또는 아래 CONFIG에 직접 입력):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   → 텔레그램
  NTFY_TOPIC                             → ntfy 앱 (텔레그램 대신 써도 됨)
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime

import requests

# ───────────────────────── 설정 ─────────────────────────
CONFIG = {
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
    "NTFY_TOPIC": os.getenv("NTFY_TOPIC", ""),
    "INTERVAL_SEC": int(os.getenv("INTERVAL_SEC", "60")),   # 확인 주기(초)
    "REMIND_SEC": int(os.getenv("REMIND_SEC", "300")),      # 재고 유지 중 재알림 주기(초)
    "TARGET_SIZE": "L",
}

PRODUCTS = {
    "솔트 그레이": 165,
    "버터 옐로우": 457,
    "블랙": 458,
    "레이크 틸": 478,
}

URL = "https://ccolore.com/product/detail.html?product_no={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ───────────────────────── 재고 판별 ─────────────────────────
def _parse_option_stock_data(html):
    """카페24 페이지에 들어있는 option_stock_data(JSON)를 꺼낸다."""
    m = re.search(r"option_stock_data\s*=\s*'(.*?)'\s*;", html, re.S)
    if not m:
        m = re.search(r'option_stock_data\s*=\s*"(.*?)"\s*;', html, re.S)
    if not m:
        return None
    raw = m.group(1)
    for attempt in (
        lambda s: json.loads(json.loads('"' + s + '"')),
        lambda s: json.loads(s.replace('\\"', '"').replace("\\/", "/")),
        lambda s: json.loads(s),
    ):
        try:
            data = attempt(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _option_matches(opt, size):
    vals = [str(opt.get("option_value", "")).strip()]
    orig = opt.get("option_value_orginal") or opt.get("option_value_original")
    if isinstance(orig, list):
        vals += [str(v).strip() for v in orig]
    return any(v.upper() == size.upper() for v in vals)


def _available_from_json(opt):
    if str(opt.get("is_selling", "T")) != "T" or str(opt.get("is_display", "T")) != "T":
        return False
    use_stock = opt.get("use_stock")
    use_stock = use_stock is True or str(use_stock) in ("T", "true", "True")
    use_soldout = str(opt.get("use_soldout", "T")) == "T"
    try:
        stock = int(float(opt.get("stock_number", 0)))
    except (TypeError, ValueError):
        stock = 0
    if use_stock and use_soldout:
        return stock > 0
    return True


def _available_from_select(html, size):
    """JSON을 못 찾을 때 대비: <option> 텍스트에서 'L [품절]' 여부로 판별."""
    found = None
    for text in re.findall(r"<option[^>]*>(.*?)</option>", html, re.S | re.I):
        t = re.sub(r"<[^>]+>", "", text).strip()
        if re.match(rf"^{re.escape(size)}(\s|\[|\(|$)", t, re.I):
            found = "품절" not in t
            if found:
                return True
    return found


def check_product(product_no, size):
    """True=구매가능, False=품절, None=판별불가"""
    r = requests.get(URL.format(product_no), headers=HEADERS, timeout=20)
    r.raise_for_status()
    html = r.text

    data = _parse_option_stock_data(html)
    if data:
        opts = [o for o in data.values() if isinstance(o, dict) and _option_matches(o, size)]
        if opts:
            return any(_available_from_json(o) for o in opts)

    return _available_from_select(html, size)


# ───────────────────────── 알림 ─────────────────────────
def notify(title, body, url=None):
    sent = False
    token, chat = CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"]
    if token and chat:
        try:
            text = f"🔔 {title}\n\n{body}" + (f"\n\n👉 {url}" if url else "")
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                timeout=15,
            ).raise_for_status()
            sent = True
        except Exception as e:
            log(f"텔레그램 전송 실패: {e}")

    topic = CONFIG["NTFY_TOPIC"]
    if topic:
        try:
            headers = {"Title": title.encode("utf-8"), "Priority": "urgent", "Tags": "rotating_light"}
            if url:
                headers["Click"] = url
            requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                          headers=headers, timeout=15).raise_for_status()
            sent = True
        except Exception as e:
            log(f"ntfy 전송 실패: {e}")

    if not sent:
        log("⚠️ 알림 채널이 설정되지 않았거나 전송 실패 — 콘솔에만 출력합니다.")
        print("\a", end="", flush=True)  # 비프음
    log(f"[알림] {title} | {body}")


# ───────────────────────── 메인 ─────────────────────────
def run_once(state):
    size = CONFIG["TARGET_SIZE"]
    now = time.time()
    for color, pno in PRODUCTS.items():
        try:
            ok = check_product(pno, size)
            state["errors"] = 0
        except Exception as e:
            state["errors"] = state.get("errors", 0) + 1
            log(f"{color}: 확인 실패 ({e})")
            if state["errors"] == 10:
                notify("모니터 오류", "10회 연속 페이지 확인 실패. 사이트 구조 변경/차단 여부를 확인하세요.")
            continue

        if ok is None:
            log(f"{color} {size}: 판별 불가 (페이지 구조 확인 필요)")
            continue

        prev = state.get(color)
        last = state.get(f"{color}_notified", 0)
        log(f"{color} {size}: {'✅ 구매 가능' if ok else '품절'}")

        if ok and (prev is not True or now - last >= CONFIG["REMIND_SEC"]):
            notify(f"에어라이트 400 {color} {size} 재고 풀림!",
                   f"{color} {size} 사이즈 품절이 해제됐어요. 지금 바로 구매하세요.",
                   URL.format(pno))
            state[f"{color}_notified"] = now
        elif prev is True and ok is False:
            log(f"{color} {size}: 다시 품절됨")
        state[color] = ok
        time.sleep(random.uniform(1, 3))  # 색상 간 간격


def main():
    args = sys.argv[1:]
    if "--test" in args:
        notify("테스트 알림", "꼴로르 재고 알림 연결이 정상입니다 👍", URL.format(458))
        return
    state = {}
    if "--once" in args:
        run_once(state)
        return
    if "--minutes" in args:  # GitHub Actions용: 정해진 시간 동안 반복 확인 후 종료
        minutes = float(args[args.index("--minutes") + 1])
        end = time.time() + minutes * 60
        log(f"{minutes}분 동안 {CONFIG['INTERVAL_SEC']}초 간격으로 감시")
        while True:
            run_once(state)
            if time.time() + CONFIG["INTERVAL_SEC"] > end:
                break
            time.sleep(CONFIG["INTERVAL_SEC"])
        log("이번 회차 감시 종료")
        return

    log(f"감시 시작: {', '.join(PRODUCTS)} / {CONFIG['TARGET_SIZE']} / {CONFIG['INTERVAL_SEC']}초 간격")
    while True:
        run_once(state)
        time.sleep(CONFIG["INTERVAL_SEC"] + random.uniform(0, 10))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("종료")
