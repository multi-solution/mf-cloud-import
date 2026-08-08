#!/usr/bin/env python3
"""
Step 0: REST API プレフライト疎通確認
====================================
スキル起動直後（Step 1のファイル走査に入る前）にREST credentialsの有効性を
確認し、Step 10（証憑自動添付）がセッション中盤で失敗するリスクを事前に潰す。

副作用ゼロ（GET /api/v3/offices を1回叩くだけ）。

使い方:
  python3 preflight.py

出力 (stdout に JSON):
  {
    "status": "ok" | "no_credentials" | "expired" | "error",
    "message": "...",
    "office_name": "..."         (status==ok時),
    "fiscal_year": NNNN          (status==ok時),
    "step10_available": true | false,
    "recovery_hint": "..."        (status!=ok時)
  }

終了コード:
  0 = ok        : Step 10 動作可。そのまま Step 1 へ
  1 = no_creds  : credentials 未整備。Step 9 まで進めるが Step 10 はスキップ
  2 = expired   : refresh_token 失効。Step 1 へ進む前に oauth_init.py で再認可
  3 = error     : 予期せぬAPI/設定エラー。要調査
"""

import argparse
import json
import sys
from pathlib import Path

# 同階層 mfc_rest を import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mfc_rest import (  # noqa: E402
    DEFAULT_CREDS,
    MfcApiError,
    get_access_token,
    http_get,
)


def _emit(obj, exit_code):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _is_expired_error(msg: str) -> bool:
    """get_access_token() の RuntimeError メッセージから失効判定"""
    lower = msg.lower()
    return (
        "invalid_grant" in lower
        or "the refresh token passed" in lower  # MFCの具体エラーメッセージ
        or "does not exist" in lower
        or "expired" in lower
        or "revoked" in lower
    )


def main():
    parser = argparse.ArgumentParser(description="REST API プレフライト疎通確認 (Step 0)")
    parser.add_argument("--creds", default=None,
                        help="credentials.json のパス。複数の事業者を扱う場合は"
                             "事業者ごとに別ファイルを指定する（既定: ~/.mfc/credentials.json）")
    args = parser.parse_args()
    creds = Path(args.creds).expanduser() if args.creds else DEFAULT_CREDS

    # ---- 1. credentials.json 存在チェック ----
    if not creds.exists():
        _emit({
            "status": "no_credentials",
            "message": f"{creds} が見つかりません",
            "step10_available": False,
            "recovery_hint": (
                "Step 10（証憑自動添付）を有効化したい場合は、"
                "scripts/oauth_init.py を実行して初回認可を行ってください。"
                "記帳のみ（Step 9まで）であればこのまま続行可能ですが、"
                "Step 9の完了報告で全件について「画面手動添付」を案内する必要があります。"
            ),
        }, exit_code=1)

    # ---- 2. access_token 取得（refresh_token 検証） ----
    try:
        access = get_access_token(creds)
    except RuntimeError as e:
        msg = str(e)
        if _is_expired_error(msg):
            _emit({
                "status": "expired",
                "message": "REST用 refresh_token が失効しています",
                "detail": msg.split("\n")[0],
                "step10_available": False,
                "recovery_hint": (
                    "プラグインの scripts/reauth.py を実行して再認可してください"
                    "（保存済みのClient ID/Secretを再利用するので、ブラウザで「許可」を"
                    "押すだけです。--creds を使っている場合は同じパスを指定）。\n"
                    "再認可せずに記帳へ進むと、記帳は成功するが証憑添付で失敗し、"
                    "孤児仕訳（証憑未添付の記帳）が発生するリスクがあります。"
                    "再認可を完了してから進めることを強く推奨。"
                ),
            }, exit_code=2)
        _emit({
            "status": "error",
            "message": f"access_token取得失敗: {msg}",
            "step10_available": False,
            "recovery_hint": (
                "credentials.json の中身を確認、もしくは oauth_init.py で再認可してください。"
            ),
        }, exit_code=3)
    except Exception as e:
        _emit({
            "status": "error",
            "message": f"credentials読込/access_token取得で予期せぬエラー: {e}",
            "step10_available": False,
            "recovery_hint": (
                f"{creds} のJSON構造を確認してください。"
                "破損している場合は oauth_init.py の再実行で再生成可能です。"
            ),
        }, exit_code=3)

    # ---- 3. API疎通テスト（GET /api/v3/offices） ----
    try:
        offices = http_get("/api/v3/offices", access)
    except MfcApiError as e:
        hint = "scopeが正しく付与されているか、APIのベースURLが正しいか確認"
        if e.status == 401:
            hint = "scope に offices.read が含まれているか確認。oauth_init.py で再認可するとscope追加可能"
        elif e.status == 403:
            hint = "アプリポータルでアプリ連携権限が付与されているか確認"
        _emit({
            "status": "error",
            "message": f"API疎通失敗: {e}",
            "http_status": e.status,
            "step10_available": False,
            "recovery_hint": hint,
        }, exit_code=3)

    # ---- 4. 事業者情報の最小抽出（表示用） ----
    office_name = None
    fiscal_year = None
    if isinstance(offices, dict):
        # 実機レスポンス構造に応じて柔軟に拾う
        office_name = offices.get("name") or offices.get("office_name")
        periods = offices.get("accounting_periods") or []
        if periods:
            # 通常先頭が最新会計期間
            fiscal_year = periods[0].get("fiscal_year")

    _emit({
        "status": "ok",
        "message": "REST API 疎通確認OK。Step 10（証憑自動添付）が動作可能です。",
        "office_name": office_name,
        "fiscal_year": fiscal_year,
        "step10_available": True,
    }, exit_code=0)


if __name__ == "__main__":
    main()
