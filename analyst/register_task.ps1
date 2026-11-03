<#
run_analyst.ps1 を Windows のタスクスケジューラに登録する（平日の毎朝）。

例:
  .\register_task.ps1 -Base "G:\マイドライブ\AI投資チーム" -Time "07:00"
  .\register_task.ps1 -Base "G:\マイドライブ\AI投資チーム" -Time "07:00" -RunClaude

削除: Unregister-ScheduledTask -TaskName "AI投資チーム_アナリスト" -Confirm:$false
注意: ログオン中のみ実行されます（Google ドライブと claude のログイン状態を使うため）。
#>
param(
    [Parameter(Mandatory = $true)][string]$Base,
    [string]$Time = "07:00",
    [string]$TaskName = "AI投資チーム_アナリスト",
    [string]$Python = "python",
    [switch]$RunClaude
)
$runner = Join-Path $PSScriptRoot "run_analyst.ps1"
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -Base `"$Base`" -Python `"$Python`""
if ($RunClaude) { $arg += " -RunClaude" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force |
    Select-Object TaskName, State
