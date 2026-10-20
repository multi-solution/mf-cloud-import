# Codex で動かすときの読み替え

このプラグインの手順書（`commands/*.md`・`skills/mf-cloud-import/SKILL.md`・`agents/tax-reviewer.md`）は
Claude Code 向けに書かれている。Codex（ChatGPT アプリの Codex・Codex CLI）で動かすときは、
**手順書の中身はそのまま守り**、Claude 固有の書き方だけを次の表のとおり読み替える。

安全装置（承認前に登録しない／証憑のない経費は記帳しない／台帳チェックを省略しない／
売掛金の回収を API で仕訳しない／事業者の三点突合）は、**Codex でも一切緩めない**。

---

## 1. プラグインの場所とスクリプトのパス

- **プラグインのルート**＝このファイル（`codex/CODEX.md`）の1階層上のフォルダ。
  `codex/skills/<名前>/SKILL.md` から見れば3階層上。
- 手順書の `${CLAUDE_PLUGIN_ROOT}` は、Codex では設定されない。
  **プラグインのルートの絶対パスに置き換えて**実行する。
  例：`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py`
  → `python3 /…/mf-cloud-import/scripts/preflight.py`
- 手順書の `<プラグインのscripts>` も同じく `<プラグインのルート>/scripts` の絶対パス。
- 作業フォルダは利用者の証憑フォルダなので、**相対パス `scripts/...` では見つからない**。

## 2. コマンド名

| 手順書の書き方 | Codex での呼び方 |
|---|---|
| `/mf-cloud-import:mf-setup` | `$mf-setup` |
| `/mf-cloud-import:mf-status` | `$mf-status` |
| `/mf-cloud-import:mf-import` | `$mf-import` |

利用者に案内するときは右の書き方を使う。スキルの一覧では `mf-cloud-import:mf-setup` のように
プラグイン名が前に付いて表示されるが、同じものである。

Codex は、プラグインの `commands/` を自動で写して `source-command-mf-status` のようなスキルを作ることがある。
これは**読み替えが入っていない写し**なので使わない。必ず `codex/skills/` の `mf-setup`・`mf-status`・
`mf-import`・`mf-cloud-import` を使う。

## 3. 税務レビュー（`tax-reviewer` エージェント）

Codex のプラグインには、レビュー役のエージェントを同梱できない。
`tax_review` が `on` のときは、承認テーブルを作った直後に次のとおり**自分で**行う。

1. `<プラグインのルート>/agents/tax-reviewer.md` を読む
2. **判断が分かれる項目だけ**を対象に、その役割・禁止事項・出力形式に**そのまま**従ってレビューする
   （数値を断定しない／節税の助言をしない／3点セットの形から外れない）
3. 結果を承認テーブルの後に示し、利用者の判断を待つ
4. レビューの段階では帳簿を変更しない

`tax_review` が `off` のときは行わない（手順書どおり、印だけ付ける）。

## 4. MF クラウドへの接続（`mfc_ca`）

手順書の「claude.ai のコネクタ設定」は、Codex では次に読み替える。

- **追加**：ChatGPT アプリの Codex の「設定 → MCP サーバー → サーバーを追加」で、
  名前 `mfc_ca`、URL `https://beta.mcp.developers.biz.moneyforward.com/mcp/ca/v3` を登録し、
  アプリを再起動してから「認証（Authenticate）」を押す。
  ターミナルなら `codex mcp add mfc_ca --url https://beta.mcp.developers.biz.moneyforward.com/mcp/ca/v3`
  の後に `codex mcp login mfc_ca`。
- **事業者を切り替える（切断してつなぎ直す）**：`codex mcp logout mfc_ca` → `codex mcp login mfc_ca`。
  認可画面で目的の事業者が選ばれていることを確認してから「許可」を押してもらう。
- ツール名の前に接続名が付いて表示されることがあるが、`mfc_ca_currentOffice` などと同じもの。
- `mfc_ca` のツールが見つからないときは、**接続が確認できるまで先へ進まない**（手順書どおり）。

## 5. 実行の許可（Codex のサンドボックス）

- 同梱スクリプトのうち `preflight.py`・`attach_voucher.py`・`reauth.py` は MF の API と通信する。
  Codex が通信の許可を求めてきたら、**何のための通信か（例：証憑の添付）を利用者に説明してから**
  許可をもらう。許可が出ないときは、その工程を止めて理由を伝える。推測で代わりの方法を取らない。
- これらのスクリプトは、通信に加えて **`~/.mfc/credentials.json`（作業フォルダの外）への書き込み**も行う
  （鍵の更新を保存するため）。**サンドボックスの外で実行する許可**を求めて実行する。
  通信だけが許可された状態で実行すると、鍵の更新が保存できず、次回から再認可（`oauth_init.py`）が必要になる。
- スクリプトが「credentials.json に書き込めない」「Permission denied」で失敗したら、**続けて再実行しない。**
  ここまでの結果を伝え、サンドボックスの外での実行を許可してもらってから1回だけやり直す。
- `oauth_init.py` は Client Secret を入力させるため、**Codex から実行しない**。
  利用者自身にターミナルで実行してもらう（手順書の案内文どおり）。
- `.mf-cloud/`（設定・台帳）は証憑フォルダの直下にできる。Codex はこのフォルダを作業フォルダとして開く。

## 6. 証憑の読み取り

- **1ファイルずつ自分で読む。** Codex のサブエージェントにも分けない（手順書の「並列委譲は禁止」と同じ）。
- 画像（JPG/PNG）は画像を表示する機能で目視して読む。
- PDF は、文字を取り出して読むか、画像に変換して目視する。
  **読み取れない・数字がはっきりしないときは推測せず、要確認として承認テーブルに載せる。**

## 7. 利用者への質問

手順書の「選択肢を出す」「一度に1〜2問」は、Codex でも同じ。
番号付きの選択肢を文章で示し、利用者の返事を待ってから次へ進む。
承認テーブルを出したら、**利用者が「実行」と答えるまで登録系のツールを呼ばない。**
