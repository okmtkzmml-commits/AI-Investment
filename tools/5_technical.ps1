# 手順5: テクニカル指標(25日・75日移動平均、RSI14日、出来高5日/25日)を計算する
# 使い方: .\technical.ps1 -codes 7974,9746
# 出来高の「ほぼ同水準」は 5日平均/25日平均 が 0.95〜1.05 を目安にしている(判定は人が行う)
param([string[]]$codes)
$h = @{"User-Agent" = "Mozilla/5.0" }
foreach ($c in $codes) {
    $r = Invoke-RestMethod "https://query1.finance.yahoo.com/v8/finance/chart/$c.T?range=1y&interval=1d" -Headers $h
    $q = $r.chart.result[0]; $ts = $q.timestamp
    $cl = @(); $vo = @(); $d = @()
    for ($i = 0; $i -lt $ts.Count; $i++) {
        if ($null -ne $q.indicators.quote[0].close[$i] -and $null -ne $q.indicators.quote[0].volume[$i]) {
            $cl += [double]$q.indicators.quote[0].close[$i]; $vo += [double]$q.indicators.quote[0].volume[$i]
            $d += [DateTimeOffset]::FromUnixTimeSeconds($ts[$i]).AddHours(9).ToString("MM-dd")
        }
    }
    $n = $cl.Count
    $ma25 = ($cl[($n - 25)..($n - 1)] | Measure-Object -Average).Average
    $ma75 = ($cl[($n - 75)..($n - 1)] | Measure-Object -Average).Average
    # RSI は Wilder 平滑化
    $g = 0; $l = 0
    for ($i = 1; $i -le 14; $i++) { $df = $cl[$i] - $cl[$i - 1]; if ($df -gt 0) { $g += $df } else { $l -= $df } }
    $ag = $g / 14; $al = $l / 14
    for ($i = 15; $i -lt $n; $i++) {
        $df = $cl[$i] - $cl[$i - 1]
        $ag = ($ag * 13 + [Math]::Max($df, 0)) / 14; $al = ($al * 13 + [Math]::Max(-$df, 0)) / 14
    }
    $rsi = if ($al -eq 0) { 100 } else { 100 - 100 / (1 + $ag / $al) }
    $v5 = ($vo[($n - 5)..($n - 1)] | Measure-Object -Average).Average
    $v25 = ($vo[($n - 25)..($n - 1)] | Measure-Object -Average).Average
    "{0} last={1} close={2:N1} ma25={3:N1} ma75={4:N1} rsi={5:N1} v5/v25={6:N3}" -f $c, $d[$n - 1], $cl[$n - 1], $ma25, $ma75, $rsi, ($v5 / $v25)
}
