#!/usr/bin/env python3
"""
処理済み台帳 — 同じ証憑を二度記帳する事故を「構造的に」防ぐ。

なぜ必要か:
  「記帳したらファイルを仕訳済フォルダへ移動する」という運用は、移動を忘れた瞬間に
  破綻する。次のセッションが未記帳と誤認し、同じ領収書をもう一度記帳してしまう。
  （実例: 12件・15,890円を二重計上する寸前まで行った）
  そこで「どのファイルを記帳したか」を人間の操作ではなくファイルの中身で管理する。

判定はファイルの内容ハッシュ(SHA-256)で行う。
そのためファイル名を変えても、フォルダを移動しても、正しく「記帳済み」と判定できる。

使い方:
  python3 ledger.py check <file>...        未記帳のファイルだけを返す（記帳前に必ず実行）
  python3 ledger.py record <file> --journal-number N [--journal-id ID]
                                           [--amount N] [--voucher-attached]
  python3 ledger.py info <file>            そのファイルの記帳情報を返す
  python3 ledger.py list                   台帳の全件を返す
  python3 ledger.py forget <file>          台帳から削除（記帳を取り消した場合のみ）

台帳の場所:
  既定は <最初のファイルのあるフォルダ>/.mf-cloud/ledger.json
  --ledger <path> で明示指定できる。事業者ごとに必ず別の台帳にすること。

出力は常にJSON。stdoutのJSONだけを解釈すること。
終了コード: 0=正常 / 1=引数エラー / 2=ファイルが見つからない
"""

from __future__ import annotations  # Python 3.8 でも新しい型注釈を書けるようにする

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER_VERSION = 1
LEDGER_DIRNAME = ".mf-cloud"
LEDGER_FILENAME = "ledger.json"


def file_digest(path: Path) -> str:
    """ファイル内容のSHA-256。名前・場所に依存しない同一性の判定に使う。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_base_dir(start: Path) -> Path | None:
    """start から上位へ .mf-cloud/ ディレクトリを探す（gitのリポジトリ探索と同じ要領）。

    領収書がサブフォルダ（処理前/処理済 など）に分かれていても、
    共通の親にある1本の台帳を見つけるための仕組み。
    フォルダごとに別の台帳ができると二重計上を検出できなくなる。
    """
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / LEDGER_DIRNAME).is_dir():
            return d
    return None


def resolve_ledger_path(explicit: str | None, files: list[Path]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    start = files[0].resolve().parent if files else Path.cwd()
    base = find_base_dir(start)
    if base is not None:
        return base / LEDGER_DIRNAME / LEDGER_FILENAME
    return start / LEDGER_DIRNAME / LEDGER_FILENAME


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"version": LEDGER_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # 壊れた台帳を黙って作り直すと二重計上の防波堤が消える。必ず止める。
        emit({"status": "error",
              "message": f"台帳が読めません: {path} ({e})",
              "recovery_hint": "台帳を手で修復するか、バックアップから戻してください。"
                               "空の台帳で上書きすると二重計上を検出できなくなります。"}, code=1)
    if "entries" not in data:
        data["entries"] = {}
    return data


def save_ledger(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 書き込み途中で壊れないよう原子的に置換


def emit(payload: dict, code: int = 0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def collect_files(raw: list[str]) -> list[Path]:
    files, missing = [], []
    for r in raw:
        p = Path(r).expanduser()
        (files if p.is_file() else missing).append(p)
    if missing:
        emit({"status": "error",
              "message": "ファイルが見つかりません",
              "missing": [str(m) for m in missing]}, code=2)
    return files


def cmd_check(args):
    files = collect_files(args.files)
    lpath = resolve_ledger_path(args.ledger, files)
    ledger_exists = lpath.exists()
    ledger = load_ledger(lpath)
    entries = ledger["entries"]

    unrecorded, already = [], []
    for f in files:
        d = file_digest(f)
        rec = entries.get(d)
        if rec:
            already.append({"file": str(f), "sha256": d[:16],
                            "journal_number": rec.get("journal_number"),
                            "recorded_at": rec.get("recorded_at"),
                            "recorded_as": rec.get("file_name"),
                            "voucher_attached": rec.get("voucher_attached", False)})
        else:
            unrecorded.append({"file": str(f), "sha256": d[:16]})

    result = {"status": "ok",
              "ledger": str(lpath),
              "ledger_exists": ledger_exists,
              "unrecorded_count": len(unrecorded),
              "already_recorded_count": len(already),
              "unrecorded": unrecorded,
              "already_recorded": already,
              "note": "already_recorded は記帳済みです。絶対に再記帳しないでください。"
                      "ファイル名が違っていても中身が同一なら同じ証憑です。"}
    if not ledger_exists:
        result["warning"] = (
            "台帳ファイルが見つかりませんでした（全件が未記帳と判定されています）。"
            "初回ならこのままで問題ありません。過去に記帳したことがあるなら、"
            "既存の台帳が別の場所にある可能性があります。--ledger で正しい台帳を"
            "指定し直してください。誤った台帳のまま進めると二重計上を検出できません。"
        )
    emit(result)


def cmd_record(args):
    files = collect_files([args.file])
    f = files[0]
    lpath = resolve_ledger_path(args.ledger, files)
    ledger = load_ledger(lpath)
    d = file_digest(f)

    prev = ledger["entries"].get(d)
    if prev and not args.force:
        emit({"status": "already_recorded",
              "message": "この証憑は既に台帳にあります。二重計上の可能性があります。",
              "existing": prev,
              "recovery_hint": "意図的な上書きなら --force を付けてください。"}, code=0)

    ledger["entries"][d] = {
        "file_name": f.name,
        "journal_number": args.journal_number,
        "journal_id": args.journal_id,
        "amount": args.amount,
        "voucher_attached": bool(args.voucher_attached),
        "recorded_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    save_ledger(lpath, ledger)
    emit({"status": "ok", "ledger": str(lpath), "sha256": d[:16],
          "entry": ledger["entries"][d], "total_entries": len(ledger["entries"])})


def cmd_info(args):
    files = collect_files([args.file])
    ledger = load_ledger(resolve_ledger_path(args.ledger, files))
    d = file_digest(files[0])
    rec = ledger["entries"].get(d)
    emit({"status": "ok", "found": rec is not None,
          "sha256": d[:16], "entry": rec})


def cmd_list(args):
    lpath = resolve_ledger_path(args.ledger, [])
    ledger = load_ledger(lpath)
    rows = [{"sha256": k[:16], **v} for k, v in ledger["entries"].items()]
    rows.sort(key=lambda r: (r.get("journal_number") or 0))
    emit({"status": "ok", "ledger": str(lpath), "count": len(rows), "entries": rows})


def cmd_forget(args):
    files = collect_files([args.file])
    lpath = resolve_ledger_path(args.ledger, files)
    ledger = load_ledger(lpath)
    d = file_digest(files[0])
    removed = ledger["entries"].pop(d, None)
    if removed:
        save_ledger(lpath, ledger)
    emit({"status": "ok", "removed": removed is not None, "entry": removed,
          "note": "MFクラウド側の仕訳は削除されません。仕訳を消した場合のみ実行してください。"})


def main():
    ap = argparse.ArgumentParser(description="処理済み台帳（二重計上の防止）")
    ap.add_argument("--ledger", help="台帳ファイルのパス（事業者ごとに分けること）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="未記帳のファイルだけを返す")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("record", help="記帳済みとして台帳に登録")
    p.add_argument("file")
    p.add_argument("--journal-number", type=int, required=True)
    p.add_argument("--journal-id")
    p.add_argument("--amount", type=int)
    p.add_argument("--voucher-attached", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("info", help="1件の記帳情報を返す")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="台帳の全件")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("forget", help="台帳から削除（記帳を取り消した場合のみ）")
    p.add_argument("file")
    p.set_defaults(func=cmd_forget)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
