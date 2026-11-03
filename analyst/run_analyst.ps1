<#
アナリストチームの定期実行用ラッパー（Windows PowerShell 5.1 / 7）

  1. analyst_data.py prepare でデータを集め、AI 用の入力ファイルを作る
  2. -RunClaude を付けた場合、Claude Code（claude -p）に analyst_team.md の仕事をさせる
  3. analyst_data.py verify で形式とマスター不変を確認する（NG なら1回だけ AI に直させる）

例:
  .\run_analyst.ps1 -Base "G:\マイドライブ\AI投資チーム"              # データ準備だけ
  .\run_analyst.ps1 -Base "G:\マイドライブ\AI投資チーム" -RunClaude   # AI 実行まで
#>
param(
    [string]$Base = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "python",
    [switch]$RunClaude,
    [string]$Claude = "claude",
    [string]$Date = ""
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = "1"

if (-not $Date) { $Date = (Get-Date).ToString("yyyyMMdd") }
$script = Join-Path $PSScriptRoot "analyst_data.py"
$runDir = Join-Path $Base "analyst_runs\$Date"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$logFile = Join-Path $runDir "run.log"
function Log($m) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m; Write-Host $line; Add-Content $logFile $line -Encoding UTF8 }

Log "prepare 開始（Base=$Base）"
$out = & $Python $script --base $Base --date $Date prepare 2>&1
$rc = $LASTEXITCODE
$out | ForEach-Object { Add-Content $logFile "$_" -Encoding UTF8 }
if ($rc -ne 0) { Log "prepare 失敗（終了コード $rc）"; exit $rc }
$inputFile = ($out | Where-Object { "$_" -like "*analyst_input_*.md" } | Select-Object -Last 1).ToString().Trim()
Log "入力ファイル: $inputFile"

if (-not $RunClaude) {
    Log "データ準備のみで終了。AI には次のように依頼してください:"
    Log "  prompts/analyst_team.md の指示を実行してください。データは $inputFile を使ってください。"
    exit 0
}

$prompt = @"
prompts/analyst_team.md の指示（仕事1〜3）を実行してください。
今朝のデータはスクリプトで取得済みです: $inputFile
- 株価・財務の数値はこのファイルを使ってください。足りない情報（決算予定・材料など）は Web 検索で補ってください。
- テクニカルの記号と総合判定はファイルの機械判定をそのまま使ってください。
- 基準の唯一の正は 00_投資プロフィール/私の投資プロフィール_v1.txt です。
- 01_朝のブリーフィング/統合スクリーニング結果.txt には絶対に書き込まないでください。
- 日付は $Date として扱ってください。
"@
$allowed = @("Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch")

Push-Location $Base
try {
    Log "Claude 実行"
    $prompt | & $Claude -p --permission-mode acceptEdits --allowedTools $allowed 2>&1 |
        ForEach-Object { Add-Content $logFile "$_" -Encoding UTF8 }

    $v = & $Python $script --base $Base --date $Date verify 2>&1
    $vrc = $LASTEXITCODE
    $v | ForEach-Object { Log "$_" }
    if ($vrc -ne 0) {
        Log "verify NG。Claude に1回だけ修正を依頼します"
        $fix = "先ほどの analyst_team.md の作業結果を検証したところ、次の問題が見つかりました。統合スクリーニング結果.txt には触れずに、該当ファイルを修正してください。`n" + ($v -join "`n")
        $fix | & $Claude -p --permission-mode acceptEdits --allowedTools $allowed 2>&1 |
            ForEach-Object { Add-Content $logFile "$_" -Encoding UTF8 }
        $v = & $Python $script --base $Base --date $Date verify 2>&1
        $vrc = $LASTEXITCODE
        $v | ForEach-Object { Log "$_" }
    }
    exit $vrc
}
finally { Pop-Location }
