# 手順6: 暦年ごとの配当額の合計を出す(減配が続いていないかの目安)
# 使い方: .\dividends.ps1 -codes 7974,9746
# 注意: 株式分割・支払い時期・その年の途中経過の影響を受けるため、確実な確認ではない
param([string[]]$codes)
$h = @{"User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36" }
foreach ($c in $codes) {
    $r = Invoke-RestMethod "https://query1.finance.yahoo.com/v8/finance/chart/$c.T?range=6y&interval=1mo&events=div" -Headers $h
    $d = $r.chart.result[0].events.dividends
    if ($d) {
        $by = @{}
        $d.PSObject.Properties.Value | ForEach-Object {
            $y = [DateTimeOffset]::FromUnixTimeSeconds($_.date).Year
            $by[$y] = [double]$by[$y] + $_.amount
        }
        "$c : " + (($by.Keys | Sort-Object | ForEach-Object { "$_=" + [math]::Round($by[$_], 1) }) -join " ")
    } else { "$c : 配当データなし" }
}
