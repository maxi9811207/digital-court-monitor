"""Digital Court 外部監看：在 GitHub 的機器上每 5 分鐘檢查一次正式站。

壞掉或恢復時通知一次（Email＋LINE），持續壞掉不會每 5 分鐘一直叫。
狀態存在 state.json（由 workflow commit 回 repo）；每週也會 commit 一次，免得 GitHub 因為 repo 沒動靜停掉排程。
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
UA = "digital-court-monitor/1.0 (+github actions)"
# (名稱, 網址, 回應裡必須出現的字)
CHECKS = [
    ("官網首頁", "https://digital-court.cc/", "Digital Court"),
    ("預約系統（Active 場館 API）", "https://digital-court.cc/active/api/venue", '"name"'),
    ("示範館 API", "https://digital-court.cc/showcase/api/venue", '"name"'),
    ("產品頁", "https://digital-court.cc/booking", "Digital Court"),
]
CERT_HOSTS = ["digital-court.cc"]
CERT_WARN_DAYS = 14
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def fetch(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            return True, body if r.status == 200 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — 逾時、DNS、TLS 都算壞
        return False, f"{type(e).__name__}: {str(e)[:120]} ({time.time() - t:.0f}s)"


def check_once() -> list[str]:
    problems = []
    for name, url, must in CHECKS:
        ok, body = fetch(url)
        if not ok:
            problems.append(f"{name}：{body}（{url}）")
        elif must not in body:
            problems.append(f"{name}：回應內容不對，找不到「{must}」（{url}）")
    return problems


def cert_days(host: str) -> float:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=15) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
        exp = datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return (exp - datetime.now(timezone.utc)).total_seconds() / 86400


def notify(subject: str, text: str) -> list[str]:
    """寄 Email（Resend）＋推 LINE。回傳失敗的通道；全部失敗時讓 workflow 失敗，GitHub 會再寄信。"""
    failed = []
    key, to = os.getenv("RESEND_API_KEY"), [x.strip() for x in os.getenv("ALERT_EMAILS", "").split(",") if x.strip()]
    if key and to:
        body = json.dumps({"from": os.getenv("MAIL_FROM", "Digital Court <noreply@dc-studio.cc>"), "to": to,
                           "subject": subject, "text": text}).encode()
        req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:  # noqa: BLE001
            failed.append(f"email: {e}")
    else:
        failed.append("email: 沒有設定")
    tok, line_to = os.getenv("LINE_TOKEN"), os.getenv("LINE_TO")
    if tok and line_to:
        body = json.dumps({"to": line_to, "messages": [{"type": "text", "text": f"{subject}\n{text}"[:4900]}]}).encode()
        req = urllib.request.Request("https://api.line.me/v2/bot/message/push", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:  # noqa: BLE001
            failed.append(f"line: {e}")
    else:
        failed.append("line: 沒有設定")
    return failed


def main() -> int:
    now = datetime.now(TPE)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    try:
        state = json.load(open(STATE_FILE))
    except (OSError, ValueError):
        state = {}

    if os.getenv("MONITOR_TEST") == "1":
        failed = notify("【Digital Court 監看】測試通知", f"{stamp} 這是外部監看的測試訊息，收到代表通知管道正常，不需要處理。")
        print("測試通知", "失敗：" + "；".join(failed) if failed else "已送出")
        return 1 if len(failed) == 2 else 0

    problems = check_once()
    if problems:  # 等 30 秒再測一次，避免網路抖一下就叫
        time.sleep(30)
        problems = check_once()
    status = "down" if problems else "up"

    cert_note = ""
    for host in CERT_HOSTS:
        try:
            d = cert_days(host)
            if d < CERT_WARN_DAYS:
                cert_note += f"{host} 的 HTTPS 憑證剩 {d:.0f} 天到期（自動續約可能壞了）。"
        except Exception as e:  # noqa: BLE001
            cert_note += f"{host} 憑證讀取失敗：{e}。"

    prev = state.get("status", "up")
    failed = []
    if status != prev:
        if status == "down":
            failed = notify("【Digital Court 監看】網站有問題", f"{stamp} 起偵測到：\n" + "\n".join(problems) +
                            "\n\n恢復時會再通知一次。")
            state["since"] = stamp
        else:
            failed = notify("【Digital Court 監看】已恢復", f"{stamp} 全部檢查恢復正常（從 {state.get('since', '?')} 開始異常）。")
            state["since"] = stamp
    week = now.strftime("%G-W%V")
    if cert_note and state.get("cert_warned") != week:
        failed += notify("【Digital Court 監看】HTTPS 憑證快到期", f"{stamp} {cert_note}")
        state["cert_warned"] = week
    state.update(status=status, week=week, problems=problems)  # 不存檢查時間：檔案只在狀態改變或換週時變動，才不會每 5 分鐘一個 commit
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(stamp, status, problems or "全部正常", cert_note, ("通知失敗：" + "；".join(failed)) if failed else "")
    return 1 if failed and len(failed) >= 2 else 0


if __name__ == "__main__":
    sys.exit(main())
