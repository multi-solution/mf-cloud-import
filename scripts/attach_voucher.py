#!/usr/bin/env python3
"""
Step 10: 証憑自動添付（エントリポイント）
========================================

スキル本体（SKILL.md の Step 10）から呼び出される実行スクリプト。
Step 9 のMCP記帳で取得した journal_id と、対応する証憑ファイルパスを受け取り、
POST /api/v3/vouchers でアップロード → GET /journals/{id} で読み戻し検証。

使い方:
  python3 attach_voucher.py <journal_id> <file_path> [--file-name <name>]

出力:
  stdout に JSON で結果を返す。スキル側はこれをパースして完了報告に使う。
  {
    "status": "ok" | "warn" | "error",
    "uploaded_voucher_file_ids": [{"file_name": "...", "file_id": "..."}],
    "journal_voucher_file_ids_after": [...],
    "verification_passed": true|false,
    "message": "..."  (status != ok 時)
  }

終了コード:
  0 = 成功 (or warn)
  1 = アップロード成功後の検証失敗 (リカバリ判断はユーザー)
  2 = 引数不正
  3 = アップロード失敗 (孤児仕訳の可能性 → スキル側でユーザーに巻き戻し相談)
  4 = credentials/認証失敗 (oauth_init.py 再実行を案内)
"""

import argparse
import json
import sys
from pathlib import Path

# 同階層の mfc_rest を import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mfc_rest import (  # noqa: E402
    MfcApiError,
    get_access_token,
    get_journal,
    post_voucher,
)


def _print(obj, exit_code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(description="MFクラウド会計 証憑自動添付 (Step 10)")
    parser.add_argument("journal_id", help="Step 9 で取得した journal.id")
    parser.add_argument("file_path", help="証憑ファイルの絶対パス")
    parser.add_argument("--file-name", default=None,
                        help="証憑名（未指定時はファイル名）. 1〜255文字")
    parser.add_argument("--creds", default=None,
                        help="credentials.json のパス。複数の事業者を扱う場合は"
                             "事業者ごとに別ファイルを指定する（既定: ~/.mfc/credentials.json）")
    args = parser.parse_args()
    creds_path = Path(args.creds).expanduser() if args.creds else None

    # access_token取得
    try:
        access = get_access_token(creds_path) if creds_path else get_access_token()
    except FileNotFoundError:
        _print({
            "status": "error",
            "message": f"{creds_path or '~/.mfc/credentials.json'} が見つかりません",
            "recovery_hint": "scripts/oauth_init.py を実行して初回認可を行ってください"
                             "（--creds を使った場合は oauth_init.py --creds で同じパスを指定）",
        }, exit_code=4)
    except Exception as e:
        _print({
            "status": "error",
            "message": f"access_token取得失敗: {e}",
            "recovery_hint": "credentials.json を確認、または oauth_init.py で再認可",
        }, exit_code=4)

    # POST /vouchers
    try:
        resp = post_voucher(args.journal_id, args.file_path, access,
                            file_name=args.file_name)
    except FileNotFoundError as e:
        _print({"status": "error", "message": str(e)}, exit_code=2)
    except ValueError as e:
        _print({"status": "error", "message": f"パラメータ不正: {e}"}, exit_code=2)
    except MfcApiError as e:
        hint = ""
        if e.status == 401:
            hint = "アクセス権限エラー。voucher.write スコープが付与されているか確認"
        elif e.status == 413:
            hint = "ペイロード過大。証憑ファイルを圧縮するか、画面手動添付に切替"
        elif e.status == 415:
            hint = "Content-Type不一致。スクリプトを更新してください"
        elif e.status == 429:
            hint = "レート制限。しばらく待って再試行"
        elif e.status >= 500:
            hint = "サーバ側エラー。時間を置いて再試行"
        _print({
            "status": "error",
            "message": f"POST /vouchers 失敗: {e}",
            "http_status": e.status,
            "recovery_hint": hint or "孤児仕訳の可能性。スキル側で巻き戻し（delete_journal）を提案",
            "journal_id_for_recovery": args.journal_id,
        }, exit_code=3)

    voucher_files = []
    if isinstance(resp, dict):
        voucher_files = resp.get("voucher_file_ids", []) or []
    new_ids = [v.get("file_id") for v in voucher_files if v.get("file_id")]

    # 読み戻し検証
    try:
        journal_resp = get_journal(args.journal_id, access)
    except Exception as e:
        _print({
            "status": "warn",
            "message": f"アップロードは成功したが読み戻し検証に失敗: {e}",
            "uploaded_voucher_file_ids": voucher_files,
            "verification_passed": False,
        }, exit_code=0)

    j = journal_resp.get("journal", journal_resp) if isinstance(journal_resp, dict) else {}
    journal_voucher_ids = j.get("voucher_file_ids") or []
    all_present = bool(new_ids) and all(nid in journal_voucher_ids for nid in new_ids)

    result = {
        "status": "ok" if all_present else "warn",
        "uploaded_voucher_file_ids": voucher_files,
        "journal_voucher_file_ids_after": journal_voucher_ids,
        "verification_passed": all_present,
    }
    if not all_present:
        result["message"] = (
            "POST成功だが、journal側のvoucher_file_idsに新IDが反映されていない"
            "（API反映遅延または別仕訳に紐付いた可能性。要確認）"
        )
    _print(result, exit_code=0 if all_present else 1)


if __name__ == "__main__":
    main()
