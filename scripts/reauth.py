#!/usr/bin/env python3
"""
OAuth 再認可（保存済み client_id/secret を再利用）
==================================================
refresh_token が失効した際に、credentials.json に保存済みの
client_id / client_secret / redirect_uri / scope を再利用して、
ブラウザで「許可」を押すだけで refresh_token を取り直す。
（初回認可は oauth_init.py。こちらは2回目以降の復旧用）

使い方:
  python3 reauth.py [--creds <credentials.jsonのパス>]

よくある失敗:
  コールバック用ポートを別のプロセスが使っていると、ブラウザで許可を
  押しても 404 ページが出て認可コードが失われる。本スクリプトは起動時に
  ポートの空きを確認し、使用中なら占有プロセスを表示して停止する。
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import subprocess
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
DEFAULT_CREDS = Path.home() / ".mfc" / "credentials.json"

_received = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _received["code"] = params["code"][0]
            self._respond(200, "認可成功。この画面は閉じて構いません。")
        elif "error" in params:
            desc = params.get("error_description", params["error"])[0]
            _received["error"] = desc
            self._respond(400, f"認可失敗: {desc}")
        else:
            # favicon等。コールバック以外は無視する（404でも認可には影響しない）
            self.send_response(404)
            self.end_headers()

    def _respond(self, status, msg):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:2em'><h2>{msg}</h2></body></html>"
            .encode("utf-8"))

    def log_message(self, *args, **kwargs):
        return


def ensure_port_free(port: int):
    """ポートが空いているか確認。使用中なら占有プロセスを表示して停止する。

    使用中のまま進むと、ブラウザの許可後に別プロセスへリダイレクトされ、
    404が表示されて認可コードが失われる（実際に起きた事故）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return  # 空いている
    print(f"✗ ポート {port} が既に使われています。", file=sys.stderr)
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            print("―― 占有しているプロセス ――", file=sys.stderr)
            print(out, file=sys.stderr)
    except Exception:
        pass
    sys.exit(f"そのプロセスを終了してから再実行してください（例: kill <PID>）。\n"
             f"※放置したまま進めると、許可を押しても404が出て認可に失敗します。")


def main():
    parser = argparse.ArgumentParser(description="OAuth 再認可（保存済みClient ID/Secret再利用）")
    parser.add_argument("--creds", default=None,
                        help="credentials.json のパス（既定: ~/.mfc/credentials.json）")
    args = parser.parse_args()
    creds_path = Path(args.creds).expanduser() if args.creds else DEFAULT_CREDS

    if not creds_path.exists():
        sys.exit(f"認証ファイルが見つかりません: {creds_path}\n"
                 "初回認可の場合は oauth_init.py を使ってください。")

    with open(creds_path, encoding="utf-8") as f:
        creds = json.load(f)

    client_id = creds.get("client_id", "").strip()
    client_secret = creds.get("client_secret", "").strip()
    redirect_uri = creds.get("redirect_uri", "http://localhost:8765/callback").strip()
    scope = creds.get("scope", "").strip()
    if not client_id or not client_secret:
        sys.exit("client_id / client_secret がありません。oauth_init.py で初回認可してください。")

    port = urllib.parse.urlparse(redirect_uri).port or 8765
    ensure_port_free(port)

    try:
        server = http.server.HTTPServer(("localhost", port), CallbackHandler)
    except OSError as e:
        sys.exit(f"localhost:{port} のlistenに失敗: {e}")
    threading.Thread(target=server.serve_forever, daemon=True).start()

    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    })
    print("ブラウザを開きます。MFクラウドにログインして「許可」を押してください。")
    print("（開かない場合は次のURLを手動で開く）\n\n" + auth_url + "\n")
    webbrowser.open(auth_url)

    deadline = time.time() + 300
    while "code" not in _received and "error" not in _received:
        if time.time() > deadline:
            server.shutdown()
            sys.exit("タイムアウト（5分）。再実行してください。")
        time.sleep(0.5)
    server.shutdown()

    if "error" in _received:
        sys.exit(f"認可エラー: {_received['error']}")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": _received["code"],
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"token endpoint失敗 (HTTP {e.code}):\n"
                 f"{e.read().decode('utf-8', errors='replace')}")

    if "refresh_token" not in tok:
        sys.exit("refresh_token が返却されませんでした。")

    creds["refresh_token"] = tok["refresh_token"]
    if tok.get("scope"):
        creds["scope"] = tok["scope"]
    with open(creds_path, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(creds_path, 0o600)
    except OSError:
        pass
    print(f"✓ refresh_token を更新しました: {creds_path}")

    # 動作確認（同梱の preflight 相当・読み取りのみ）
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mfc_rest import get_access_token, http_get
        access = get_access_token(creds_path)
        http_get("/api/v3/offices", access)
        print("✓ API疎通成功。証憑の自動添付が使えるようになりました。")
    except Exception as e:
        print(f"（動作確認は失敗しましたが refresh_token は保存済みです: {e}）")


if __name__ == "__main__":
    main()
