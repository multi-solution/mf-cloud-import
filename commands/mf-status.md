---
description: いま何が終わって何が残っているかを1画面で表示します（帳簿は変更しません）
---

# 状況の確認

**読み取りだけを行う。登録・修正・添付は一切しない。**
利用者の最大の不安は「何が終わって何が残っているか分からない」こと。ここを1画面で解消する。

## 集める情報

`.mf-cloud/config.json` を読み、無ければ `/mf-cloud-import:mf-setup` を案内して終了する。

1. **接続の健康診断**
   - `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py` … 証憑添付が使えるか
   - `mfc_ca_currentOffice` … 事業者名・種別・会計期間
   - **config の `office_name` と一致するか照合する。違えば最優先で警告する**

2. **未仕訳の連携明細**
   `mfc_ca_getTransactions(journalizing_statuses:["none"], per_page:1000)`
   → `metadata.total_count` と、口座別の内訳

3. **証憑待ち**
   対象フォルダのファイルを `ledger.py check` にかけ、`unrecorded` の件数
   （＝証憑はあるが、まだ記帳していないもの）。
   **`--ledger` は config.json と同じ `.mf-cloud/ledger.json` を明示指定**し、
   出力の `warning`（台帳が見つからない）が出ていないか確認する

4. **記帳済みの累計**
   `ledger.py list --ledger <同上>` の件数

5. **入金予定（未実現仕訳）**
   `mfc_ca_getJournals(is_realized:false)` の件数
   → **画面の「実現」で消し込む必要があるもの。APIでは処理できない**

## 表示

### `beginner` のとき

```
【いまの状況】  ◯◯（個人事業・2026年度）

  未仕訳の明細        17件
    ・カードA           6件
    ・銀行B             9件
    ・その他            2件

  領収書はあるが未記帳  5件   → 「記帳して」で処理できます
  記帳済み（累計）     42件

  入金の消し込み待ち    8件   ⚠️ MFの画面で「実現」の操作が必要です
                              （私からは操作できません）

  証憑の自動添付       ✅ 使えます
```

### `expert` のとき

```
office: ◯◯ (INDIVIDUAL, FY2026)   voucher_api: OK

  未仕訳 transactions : 17  [カードA 6 / 銀行B 9 / その他 2]
  未記帳 vouchers     :  5
  台帳 entries        : 42
  未実現 journals     :  8  ← 画面の実現でのみ消込可（API不可）
```

## 注意

- **数字を出すだけで終わらせない。** 次に何をすればよいかを1行添える
- 未実現仕訳が1件以上あるときは、**「APIでは処理できない」ことを必ず明記する**
  （黙って件数だけ出すと、自動で処理されると誤解される）
- 事業者名が config と食い違う場合は、**他の情報より先に、目立つ形で警告する**
- 証憑添付が使えない場合は、**直し方（`oauth_init.py` の実行）をその場に書く**
