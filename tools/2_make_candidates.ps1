# 手順2: universe.txt の業種(Yahoo分類)を、プロフィールの5セクターに振り分けて cands.txt を作る
# 使い方: .\make_candidates.ps1 -sd <作業フォルダー>
param([string]$sd)
$inc = @{
    "Semiconductors" = "半導体・電子部品"; "Semiconductor Equipment & Materials" = "半導体・電子部品"; "Electronic Components" = "半導体・電子部品"
    "Electronic Gaming & Multimedia" = "ゲーム"
    "Information Technology Services" = "IT"; "Software - Application" = "IT"; "Software - Infrastructure" = "IT"
    "Apparel Retail" = "消費財・小売"; "Department Stores" = "消費財・小売"; "Footwear & Accessories" = "消費財・小売"
    "Home Improvement Retail" = "消費財・小売"; "Internet Retail" = "消費財・小売"; "Specialty Retail" = "消費財・小売"
    "Discount Stores" = "消費財・小売"; "Grocery Stores" = "消費財・小売"; "Household & Personal Products" = "消費財・小売"
    "Packaged Foods" = "消費財・小売"; "Beverages - Brewers" = "消費財・小売"; "Beverages - Non-Alcoholic" = "消費財・小売"
    "Confectioners" = "消費財・小売"; "Apparel Manufacturing" = "消費財・小売"; "Furnishings, Fixtures & Appliances" = "消費財・小売"
    "Tobacco" = "消費財・小売"; "Consumer Electronics" = "消費財・小売"
    "Auto Manufacturers" = "自動車"; "Auto Parts" = "自動車"
}
$rows = Get-Content "$sd\universe.txt" | Where-Object { $_ } | ForEach-Object {
    $p = $_ -split '\|'
    if ($inc.ContainsKey($p[3])) { "{0}|{1}|{2}|{3}|{4}" -f $p[0], $p[1], $inc[$p[3]], $p[3], $p[4] }
}
Set-Content "$sd\cands.txt" $rows -Encoding UTF8
"候補 $($rows.Count) 銘柄"
