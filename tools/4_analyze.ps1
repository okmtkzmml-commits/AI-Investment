param([string]$sd)
$recs = Get-Content "$sd\fund4.jsonl" | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json }
function Last2($arr) { $a = @($arr | Sort-Object { $_.d }); return $a }
$rows = foreach ($r in $recs) {
    $ts = $r.ts
    $qa = Last2 $ts.quarterlyTotalAssets; $qe = Last2 $ts.quarterlyStockholdersEquity
    $aa = Last2 $ts.annualTotalAssets; $ae = Last2 $ts.annualStockholdersEquity
    $eq = $null
    if ($qa.Count -gt 0 -and $qe.Count -gt 0 -and $qa[-1].d -eq $qe[-1].d -and $qa[-1].v) { $eq = $qe[-1].v / $qa[-1].v * 100 }
    elseif ($aa.Count -gt 0 -and $ae.Count -gt 0 -and $aa[-1].v) { $eq = $ae[-1].v / $aa[-1].v * 100 }
    $rev = Last2 $ts.annualTotalRevenue; $op = Last2 $ts.annualOperatingIncome
    $rg = $null; $og = $null; $opl = $null
    if ($rev.Count -ge 2 -and $rev[-2].v) { $rg = ($rev[-1].v / $rev[-2].v - 1) * 100 }
    if ($op.Count -ge 2 -and $op[-2].v -gt 0) { $og = ($op[-1].v / $op[-2].v - 1) * 100 }
    if ($op.Count -ge 1) { $opl = $op[-1].v }
    [pscustomobject]@{
        code = $r.code; name = $r.name; sector = $r.sector; ind = $r.ind; mcap = $r.mcap
        pe = $r.pe; pb = $r.pb; yld = $(if ($null -ne $r.yld) { $r.yld * 100 } else { $null }); payout = $(if ($null -ne $r.payout) { $r.payout * 100 } else { $null })
        beta = $r.beta; tv = $(if ($r.avgvol -and $r.price) { $r.avgvol * $r.price / 1e8 } else { $null })
        roe = $(if ($null -ne $r.roe) { $r.roe * 100 } else { $null }); opm = $(if ($null -ne $r.opm) { $r.opm * 100 } else { $null })
        eq = $eq; rg = $rg; og = $og; epsg = $(if ($null -ne $r.epsg) { $r.epsg * 100 } else { $null }); opl = $opl; earn = $r.earn
    }
}
# sector medians for PER / PBR (positive values)
$med = @{}
foreach ($g in ($rows | Group-Object sector)) {
    $pes = @($g.Group | Where-Object { $_.pe -gt 0 } | ForEach-Object { $_.pe } | Sort-Object)
    $pbs = @($g.Group | Where-Object { $_.pb -gt 0 } | ForEach-Object { $_.pb } | Sort-Object)
    $med[$g.Name] = @{ pe = $pes[[int]($pes.Count / 2)]; pb = $pbs[[int]($pbs.Count / 2)] }
}
$med.GetEnumerator() | ForEach-Object { "median $($_.Key) PER=$($_.Value.pe) PBR=$($_.Value.pb)" }
$stage = @{}
$pass = foreach ($x in $rows) {
    $why = $null
    if ($null -eq $x.eq -or $x.eq -lt 40) { $why = "eq" }
    elseif ($null -eq $x.opl -or $x.opl -le 0) { $why = "oploss" }
    elseif ($null -eq $x.tv -or $x.tv -lt 3) { $why = "liq" }
    elseif ($null -eq $x.rg -or $x.rg -lt 5) { $why = "revg" }
    elseif (-not (($null -ne $x.og -and $x.og -gt 0) -or ($null -ne $x.epsg -and $x.epsg -gt 0))) { $why = "profitg" }
    else {
        $exc = ($x.rg -ge 15 -and (($null -ne $x.og -and $x.og -ge 15)))
        if ($null -eq $x.yld -or ($x.yld -lt 1.5 -and -not $exc)) { $why = "yield" }
        elseif ($null -ne $x.payout -and $x.payout -gt 70) { $why = "payout" }
        elseif ($null -eq $x.pe -or $x.pe -le 0 -or $x.pe -gt 1.3 * $med[$x.sector].pe) { $why = "per" }
        elseif ($null -eq $x.pb -or $x.pb -gt 2 * $med[$x.sector].pb) { $why = "pbr" }
    }
    if ($why) { $stage[$why] = 1 + [int]$stage[$why] } else { $x }
}
"candidates=$($rows.Count) pass=$(@($pass).Count)"
$stage.GetEnumerator() | ForEach-Object { "fail $($_.Key): $($_.Value)" }
function PRank($list, $prop, $desc) {
    $sorted = @($list | Sort-Object $prop -Descending:$desc)
    $n = $sorted.Count
    $m = @{}
    for ($i = 0; $i -lt $n; $i++) { $m[$sorted[$i].code] = 1 - ($i / [math]::Max(1, $n - 1)) }
    return $m
}
$pl = @($pass)
$rEq = PRank $pl eq $true; $rGr = PRank $pl rg $true; $rYl = PRank $pl yld $true; $rPe = PRank $pl pe $false
$scored = foreach ($x in $pl) {
    $s = 0.4 * $rEq[$x.code] + 0.3 * $rGr[$x.code] + 0.2 * $rYl[$x.code] + 0.1 * $rPe[$x.code]
    if ($null -ne $x.beta -and $x.beta -gt 1.5) { $s -= 0.1 }
    $x | Add-Member -NotePropertyName score -NotePropertyValue $s -PassThru
}
$scored | Sort-Object score -Descending | ForEach-Object {
    "{0:N3} {1} {2} [{3}] PER={4:N1} ROE={5:N1} OPM={6:N1} RG={7:N1} OG={8:N1} EQ={9:N1} YLD={10:N2} PAY={11:N0} BETA={12:N2} TV={13:N1}億 earn={14}" -f $_.score, $_.code, $_.name, $_.sector, $_.pe, $_.roe, $_.opm, $_.rg, $_.og, $_.eq, $_.yld, $_.payout, $_.beta, $_.tv, $_.earn
} | Select-Object -First 40
