$sd=$args[0]
$ws=New-Object Microsoft.PowerShell.Commands.WebRequestSession
$h=@{"User-Agent"="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
try{Invoke-WebRequest "https://fc.yahoo.com" -WebSession $ws -Headers $h -UseBasicParsing -ErrorAction Stop|Out-Null}catch{}
$crumb=Invoke-RestMethod "https://query1.finance.yahoo.com/v1/test/getcrumb" -WebSession $ws -Headers $h
$o="$sd\universe.txt"; Set-Content $o "" -Encoding UTF8
foreach($c in 1300..9999){
 try{ $r=Invoke-RestMethod "https://query2.finance.yahoo.com/v10/finance/quoteSummary/$c.T?modules=assetProfile,summaryDetail,price&crumb=$crumb" -WebSession $ws -Headers $h -TimeoutSec 20
  $x=$r.quoteSummary.result[0]
  if($x.summaryDetail.marketCap.raw -ge 50000000000){ Add-Content $o ("{0}|{1}|{2}|{3}|{4}" -f $c,$x.price.shortName,$x.assetProfile.sector,$x.assetProfile.industry,$x.summaryDetail.marketCap.raw) -Encoding UTF8 }
 }catch{ if($_.Exception.Message -match '429|401'){ Start-Sleep 5 } }
}
Set-Content "$sd\universe_done.txt" "done"
