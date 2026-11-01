# analyst: アナリストチームの定期実行

`prompts/analyst_team.md`（仕事1 ファンダ → 仕事2 テクニカル → 仕事3 新規提案）を毎朝回すためのスクリプトです。

役割分担:

| 作業 | 担当 |
|---|---|
| 銘柄一覧・財務・株価の取得、定量フィルター、点数化 | スクリプト |
| テクニカルの ◎〇× と総合判定（基準どおりの機械計算） | スクリプト |
| 監視リスト・保留銘柄の抽出、前回の提案と連続日数 | スクリプト |
| 統合スクリーニング結果.txt のハッシュ記録（読むだけ） | スクリプト |
| 定性判断（材料・減配・セクター集中）、最終10銘柄、コメント、提案理由、3ファイルの保存 | AI |
| 保存後の形式チェック・マスター不変の確認 | スクリプト（verify） |

## 必要なもの

- Python 3.9 以上（追加ライブラリ不要）
- AI まで自動で回す場合: Claude Code（`claude` コマンド）にログイン済みであること

## 使い方

```powershell
cd <このリポジトリ>\analyst
$base = "G:\マイドライブ\AI投資チーム"

# 1) データ準備 → analyst_runs\YYYYMMDD\analyst_input_YYYYMMDD.md ができる
python analyst_data.py --base $base prepare

# 2) AI に依頼（手動の場合）
#   「prompts/analyst_team.md の指示を実行してください。データは analyst_runs/YYYYMMDD/analyst_input_YYYYMMDD.md を使ってください」

# 3) AI の保存結果を確認
python analyst_data.py --base $base verify
```

`--base` を省略すると、このリポジトリ自体を AI投資チームのフォルダーとして扱います。

### まとめて実行・定期実行

```powershell
.\run_analyst.ps1 -Base $base              # 1 だけ（入力ファイルを作って終わり）
.\run_analyst.ps1 -Base $base -RunClaude   # 1→2→3 を自動。verify が NG なら1回だけ AI に直させる

# 平日 7:00 にタスクスケジューラで実行
.\register_task.ps1 -Base $base -Time "07:00" -RunClaude
```

ログは `analyst_runs\YYYYMMDD\run.log` に出ます。Mac/Linux なら cron に
`python3 analyst/analyst_data.py --base <フォルダー> prepare` を登録してください。

## 所要時間とキャッシュ

- 銘柄一覧（コード 1300〜9999 の全探索、約8,700回アクセス）は **初回と7日ごと** にだけ作り直します（15分以上）。
  キャッシュは `%LOCALAPPDATA%\ai-investment-analyst`（Mac/Linux は `~/.cache/ai-investment-analyst`）。
- 財務データ（約400銘柄）は毎回取得します（10分程度）。
- 試すときは `prepare --limit 30` で財務取得を30銘柄に絞れます。

## 出力（analyst_runs\YYYYMMDD\）

| ファイル | 内容 |
|---|---|
| `analyst_input_YYYYMMDD.md` | AI に渡す入力（これを読ませる） |
| `analyst_input.json` | 同じ内容の JSON（verify が使う） |
| `fundamentals_raw.json` | 取得した生データ |
| `master_snapshot_統合スクリーニング結果.txt` / `master_sha256.txt` | 実行開始時のマスターのコピーとハッシュ |
| `run.log` | run_analyst.ps1 のログ |

## 基準の調整

定量フィルターの数値は `analyst_data.py` 冒頭の `CRITERIA`・`WEIGHTS`・`SECTOR_MAP` にまとめています
（`tools/4_analyze.ps1` と同じ解釈）。プロフィールを変えたときはここも合わせてください。

## 注意

- データ元は Yahoo Finance の非公式 API です（`tools/README.md` の「既知の問題」参照）。
  利回りの誤り・決算期変更による異常な売上成長などは入力ファイルの「データ注意」に自動で出します。
- スクリプトは `統合スクリーニング結果.txt` を読むだけで、書き込みません。
- テスト: `python -m unittest analyst/test_analyst_data.py`（ネット不要）
