#!/usr/bin/env python3
"""
MFクラウド OAuth 初回認可フロー
================================
Step 10（証憑自動添付）で使うrefresh_tokenを初回1回だけ取得する。

使い方:
  python3 oauth_init.py

事前準備:
  アプリポータルで連携アプリを登録し、Client ID と Client Secret を取得しておくこと。
  リダイレクトURI は http://localhost:8765/callback で登録、
  クライアント認証方式は CLIENT_SECRET_POST で登録されている前提。

挙動:
  1. Client ID と Client Secret を対話入力で受け取る（履歴に残さない）
  2. localhost:8765 で待受サーバ起動
  3. authorize URLをブラウザで開く（同意画面）
  4. callbackで code を受信 → token endpoint で交換
  5. refresh_token を ~/.mfc/credentials.json に保存（パーミッション 600）

依存: 標準ライブラリのみ（pipインストール不要）
"""

import getpass
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTHORIZE_URL = "https://api.biz.moneyforward.com/authorize"
TOKEN_URL = "https://api.biz.moneyforward.com/token"
REDIRECT_URI = "http://localhost:8765/callback"
CREDS_PATH = Path.home() / ".mfc" / "credentials.json"

# 初回認可で要求するスコープ（将来必要になりそうなものも含めて広めに）
SCOPES = " ".join([
    "mfc/accounting/journal.read",
    "mfc/accounting/journal.write",      # 仕訳の登録・更新・削除
    "mfc/accounting/voucher.write",      # 証憑の登録・削除（Step 10の本命）
    "mfc/accounting/offices.read",
    "mfc/accounting/accounts.read",
    "mfc/accounting/departments.read",
    "mfc/accounting/taxes.read",
    "mfc/accounting/sub_accounts.read",
    "mfc/accounting/trade_partners.read",
    "mfc/accounting/trade_partners.write",
    "mfc/accounting/connected_account.read",
    "mfc/accounting/report.read",
    "mfc/accounting/transaction.write",
])

_received = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """OAuth コールバック受信用の最小HTTPハンドラ"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _received["code"] = params["code"][0]
            self._respond_html(200, "認可成功。ターミナルに戻ってください。")
        elif "error" in params:
            desc = params.get("error_description", params["error"])[0]
            _received["error"] = desc
            self._respond_html(400, f"認可失敗: {desc}")
        else:
            self.send_response(404)
            self.end_headers()

    def _respond_html(self, status, msg):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = f"<html><body style='font-family:sans-serif;padding:2em'><h2>{msg}</h2></body></html>"
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, *args, **kwargs):
        return  # 黙る


def main():
    global CREDS_PATH
    import argparse
    parser = argparse.ArgumentParser(description="MFクラウド OAuth 初回認可フロー")
    parser.add_argument("--creds", default=None,
                        help="credentials.json の保存先。複数の事業者を扱う場合は"
                             "事業者ごとに別ファイルにする（既定: ~/.mfc/credentials.json）")
    args = parser.parse_args()
    if args.creds:
        CREDS_PATH = Path(args.creds).expanduser()

    print("=" * 60)
    print("MFクラウド OAuth 初回認可フロー")
    print("=" * 60)
    print(f"\n保存先: {CREDS_PATH}")
    print("\nアプリポータルで取得した Client ID / Secret を入力してください。")
    print("Secretは入力時に表示されません（getpass）。\n")

    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()

    if not client_id or not client_secret:
        sys.exit("Client ID / Secret が空です。中断します。")

    # ポート占有チェック（IPv6側だけ他プロセスが掴んでいるとbindは成功してしまい、
    # ブラウザの許可後に別プロセスへ流れて404になる＝認可コードが失われる。実際に起きた事故）
    import socket
    import subprocess
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        if _s.connect_ex(("127.0.0.1", 8765)) == 0:
            print("✗ ポート 8765 が既に使われています。", file=sys.stderr)
            try:
                out = subprocess.run(["lsof", "-nP", "-iTCP:8765", "-sTCP:LISTEN"],
                                     capture_output=True, text=True, timeout=10).stdout.strip()
                if out:
                    print(out, file=sys.stderr)
            except Exception:
                pass
            sys.exit("占有しているプロセスを終了してから再実行してください（例: kill <PID>）。")

    # コールバック待受開始
    try:
        server = http.server.HTTPServer(("localhost", 8765), CallbackHandler)
    except OSError as e:
        sys.exit(
            f"localhost:8765 のlistenに失敗: {e}\n"
            "ポートが他で使用中の可能性があります。"
        )

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # authorize URL を開く
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
    print("\nブラウザを開きます。MFクラウドにログイン → 同意画面で承認してください。")
    print("（ブラウザが開かない場合は以下のURLを手動で開いてください）\n")
    print(auth_url + "\n")
    webbrowser.open(auth_url)

    # 認可コード待機（最大5分）
    deadline = time.time() + 300
    while "code" not in _received and "error" not in _received:
        if time.time() > deadline:
            server.shutdown()
            sys.exit("タイムアウト（5分）。中断します。")
        time.sleep(0.5)

    server.shutdown()

    if "error" in _received:
        sys.exit(f"認可エラー: {_received['error']}")

    code = _received["code"]
    print("認可コード取得成功。token endpointへ交換します...\n")

    # CLIENT_SECRET_POST 方式で token endpoint へ
    token_body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.exit(f"token endpoint失敗 (HTTP {e.code}):\n{body}")

    if "refresh_token" not in tok:
        sys.exit(
            f"refresh_token が返却されませんでした。レスポンス:\n{json.dumps(tok, ensure_ascii=False, indent=2)}"
        )

    # 保存
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    creds = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": tok["refresh_token"],
        "scope": tok.get("scope", SCOPES),
    }
    with open(CREDS_PATH, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CREDS_PATH, 0o600)
    except OSError:
        pass  # Windows等で失敗しても致命的ではない

    print(f"✓ credentials保存: {CREDS_PATH}")
    print(f"  パーミッション: 600 (本人のみ読み書き可)\n")

    # 動作確認: 事業者情報を1回取得
    print("動作確認: 事業者情報(/api/v3/offices)を取得中...")
    try:
        # 同階層 mfc_rest.py がimport可能なら使う
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mfc_rest import get_access_token, http_get  # noqa
        access = get_access_token()
        offices = http_get("/api/v3/offices", access)
        print("✓ API疎通成功\n")
        print(json.dumps(offices, ensure_ascii=False, indent=2)[:800])
        print("\n初回認可完了。Step 10が使えるようになりました。")
    except ImportError:
        print("（mfc_rest.py が同階層にないため動作確認はスキップ。credentialsは保存済み）")
    except Exception as e:
        print(f"動作確認失敗（credentialsは保存済み）: {e}")
        print("scopeに offices.read が含まれているか確認してください。")


if __name__ == "__main__":
    main()
