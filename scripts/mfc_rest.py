#!/usr/bin/env python3
"""
MFクラウド会計REST APIヘルパー
==============================
Step 10（証憑自動添付）およびエラーリカバリ用のRESTラッパー。

提供関数:
  get_access_token()    : refresh_tokenからaccess_tokenを発行（rotation対応）
  http_get(path, token) : 任意のGETエンドポイント呼び出し
  post_voucher(...)     : POST /api/v3/vouchers
  get_journal(...)      : GET  /api/v3/journals/{id}
  delete_journal(...)   : DELETE /api/v3/journals/{id} (MCPに無いがRESTに在る)
  delete_voucher(...)   : DELETE /api/v3/vouchers

依存: 標準ライブラリのみ
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.biz.moneyforward.com/token"
# 会計API本体のベースURL（OAuthとは別ドメイン。YAML servers セクションで確定済み: 2026-05-19）
API_BASE = "https://api-accounting.moneyforward.com"  # /api/v3/... を後ろに付ける
DEFAULT_CREDS = Path.home() / ".mfc" / "credentials.json"


class MfcApiError(RuntimeError):
    """REST API呼び出しエラー（HTTPステータス・本文を保持）"""

    def __init__(self, status, method, path, body):
        self.status = status
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"{method} {path} 失敗 (HTTP {status}): {body}")


# ---------- credentials ----------

def _load_creds(creds_path=DEFAULT_CREDS):
    with open(creds_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_creds(creds, creds_path=DEFAULT_CREDS):
    with open(creds_path, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)


# ---------- access_token ----------

def get_access_token(creds_path=DEFAULT_CREDS):
    """refresh_tokenからaccess_tokenを取得。
    新しいrefresh_tokenが返ったら credentials.json に自動保存（rotation対応）。
    """
    creds = _load_creds(creds_path)
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": creds["refresh_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"refresh失敗 (HTTP {e.code}): {err_body}\n"
            "対処: oauth_init.py を再実行して認可をやり直してください。"
        ) from e

    # refresh_token rotation 対応
    new_rt = tok.get("refresh_token")
    if new_rt and new_rt != creds["refresh_token"]:
        creds["refresh_token"] = new_rt
        _save_creds(creds, creds_path)

    if "access_token" not in tok:
        raise RuntimeError(
            f"access_token が返却されませんでした: {json.dumps(tok, ensure_ascii=False)}"
        )
    return tok["access_token"]


# ---------- 汎用リクエスト ----------

def _request(method, path, access_token, body=None):
    url = API_BASE + path
    data = None
    headers = {"Authorization": f"Bearer {access_token}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode("utf-8")
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": text}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise MfcApiError(e.code, method, path, err_body) from e


def http_get(path, access_token):
    """任意のGETエンドポイント呼び出し（疎通確認等）"""
    return _request("GET", path, access_token)


# ---------- 高水準API ----------

def post_voucher(journal_id, file_path, access_token, file_name=None):
    """証憑ファイル1件をjournal_idの仕訳に添付してアップロード。

    Args:
        journal_id: 添付先の仕訳ID（Noneでも可だが、本スキルでは必ず指定する想定）
        file_path: ローカルのファイルパス（PDF/JPG/PNG等）
        access_token: アクセストークン
        file_name: 証憑名（未指定時はファイル名から自動）

    Returns:
        APIレスポンス（voucher_file_ids[] を含む辞書）
    """
    fp = Path(file_path)
    if not fp.exists():
        raise FileNotFoundError(f"証憑ファイルが存在しません: {file_path}")

    file_data = base64.b64encode(fp.read_bytes()).decode("ascii")
    name = file_name or fp.name
    if len(name) < 1 or len(name) > 255:
        raise ValueError(f"file_name は1〜255文字: {name!r}")

    body = {
        "journal_id": journal_id,
        "voucher_files": [
            {
                "file_name": name,
                "file_data": file_data,
            }
        ],
    }
    return _request("POST", "/api/v3/vouchers", access_token, body)


def get_journal(journal_id, access_token):
    """仕訳の現在状態を取得（voucher_file_ids 読み戻し検証用）"""
    return _request("GET", f"/api/v3/journals/{journal_id}", access_token)


def delete_journal(journal_id, access_token):
    """仕訳を削除（MCPには無いがRESTにある）。
    主に: voucher POST失敗時の孤児仕訳の巻き戻し、ドライラン後始末。
    呼び出し前にユーザー承認を必須にすること。
    """
    return _request("DELETE", f"/api/v3/journals/{journal_id}", access_token)


def delete_voucher(journal_id, voucher_file_id, access_token):
    """証憑添付の解除"""
    body = {
        "journal_id": journal_id,
        "voucher_file_id": voucher_file_id,
    }
    return _request("DELETE", "/api/v3/vouchers", access_token, body)
