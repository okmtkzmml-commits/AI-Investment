param([string]$sd, [int]$limit = 100000)
$ws = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$h = @{"User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36" }
try { Invoke-WebRequest "https://fc.yahoo.com" -WebSession $ws -Headers $h -UseBasicParsing -ErrorAction Stop | Out-Null } catch {}
$crumb = Invoke-RestMethod "https://query1.finance.yahoo.com/v1/test/getcrumb" -WebSession $ws -Headers $h
$out = "$sd\fund4.jsonl"
$log = "$sd\err4.txt"
Set-Content $out "" -Encoding UTF8
Set-Content $log "" -Encoding UTF8
function Get-Raw($v) { if ($null -ne $v -and $null -ne $v.raw) { return $v.raw } else { return $null } }
$lines = @(Get-Content "$sd\cands.txt" | Where-Object { $_ }) | Select-Object -First $limit
foreach ($line in $lines) {
    $p = $line -split '\|'
    $code = $p[0]
    try {
        $r = Invoke-RestMethod "https://query2.finance.yahoo.com/v10/finance/quoteSummary/$code.T?modules=financialData,summaryDetail,defaultKeyStatistics,calendarEvents&crumb=$crumb" -WebSession $ws -Headers $h -TimeoutSec 20
        $x = $r.quoteSummary.result[0]
        $tsr = Invoke-RestMethod "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/$code.T?type=annualStockholdersEquity,annualTotalAssets,annualOperatingIncome,annualTotalRevenue,quarterlyStockholdersEquity,quarterlyTotalAssets&merge=false&period1=1600000000&period2=1793300000" -Headers $h -TimeoutSec 20
        $series = @{}
        foreach ($res in $tsr.timeseries.result) {
            $k = $res.meta.type[0]
            $vals = $res.$k
            if ($vals) { $series[$k] = @($vals | ForEach-Object { @{ d = $_.asOfDate; v = $_.reportedValue.raw } }) }
        }
        $ed = $null
        try { $ed = $x.calendarEvents.earnings.earningsDate[0].fmt } catch {}
        $rec = [ordered]@{
            code = $code; name = $p[1]; sector = $p[2]; ind = $p[3]; mcap = [double]$p[4]
            pe = (Get-Raw $x.summaryDetail.trailingPE); fpe = (Get-Raw $x.summaryDetail.forwardPE)
            pb = (Get-Raw $x.defaultKeyStatistics.priceToBook); yld = (Get-Raw $x.summaryDetail.dividendYield)
            payout = (Get-Raw $x.summaryDetail.payoutRatio); beta = (Get-Raw $x.summaryDetail.beta)
            avgvol = (Get-Raw $x.summaryDetail.averageVolume); price = (Get-Raw $x.financialData.currentPrice)
            roe = (Get-Raw $x.financialData.returnOnEquity); opm = (Get-Raw $x.financialData.operatingMargins)
            revg = (Get-Raw $x.financialData.revenueGrowth); epsg = (Get-Raw $x.financialData.earningsGrowth)
            earn = $ed; ts = $series
        }
        Add-Content $out ($rec | ConvertTo-Json -Depth 5 -Compress) -Encoding UTF8
    }
    catch {
        Add-Content $log ("$code " + $_.Exception.Message + " line " + $_.InvocationInfo.ScriptLineNumber) -Encoding UTF8
        if ($_.Exception.Message -match '429|401') { Start-Sleep 5 }
    }
}
Set-Content "$sd\fund4_done.txt" "done"
