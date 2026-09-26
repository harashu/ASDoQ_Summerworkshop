param([string]$RunDir, [string]$CaseDir, [string]$KnowledgeDir)
# review.py の判断の記録の再計算チェック と evaluate.py の照合を PowerShell で再現する（試行用）

$utf8 = New-Object System.Text.UTF8Encoding($false)
function Norm([string]$s) { if (-not $s) { return '' }; return (($s -replace '\s', '').Trim('「', '」', '『', '』', '"', '“', '”')) }
function TextMatch([string]$quote, [string]$target) {
  $q = Norm $quote; $t = Norm $target
  if (-not $q -or -not $t) { return $false }
  return ($q.Contains($t) -or ($t.Contains($q) -and $q.Length -ge 0.5 * $t.Length))
}
function MatchesDefect($f, $d) {
  $text = "$($f.issue) $($f.raw_comment)"
  $hit = $false; foreach ($t in @($d.match_texts)) { if ($t -and (TextMatch $f.quote $t)) { $hit = $true } }
  if ($hit) {
    if (ClassOk $f.sub_characteristic $d) { return $true }
    foreach ($k in @($d.evidence_any)) { if ($k -and $text.Contains($k)) { return $true } }
  }
  foreach ($k in @($d.mention_any)) { if ($k -and $text.Contains($k)) { return $true } }
  return $false
}
function ClassOk([string]$sub, $d) {
  foreach ($c in ($sub -split '[／/]')) { if ($d.accepted_sub_characteristics -contains $c.Trim()) { return $true } }; return $false
}

$key = [IO.File]::ReadAllText("$CaseDir\answer_key.json", $utf8) | ConvertFrom-Json
$findings = @(([IO.File]::ReadAllText("$RunDir\findings.json", $utf8) | ConvertFrom-Json) | ForEach-Object { $_ })
$trace = [IO.File]::ReadAllText("$RunDir\trace.json", $utf8) | ConvertFrom-Json
$byId = @{}; foreach ($f in $findings) { $byId[$f.finding_id] = $f }
$defById = @{}; foreach ($d in $key.defects) { $defById[$d.id] = $d }
$n = $key.defects.Count

# --- 判断の記録の再計算チェック ---
$kids = @()
foreach ($kf in Get-ChildItem "$KnowledgeDir\*.md") { $kids += [regex]::Matches([IO.File]::ReadAllText($kf.FullName, $utf8), '(?m)^### (K-[A-Z]+-\d+)') | ForEach-Object { $_.Groups[1].Value } }
$problems = @(); $checked = 0
foreach ($it in $trace.items) {
  $srcs = @($it.source_findings)
  $unknown = @($srcs | Where-Object { -not $byId.ContainsKey($_) })
  if ($unknown.Count) { $problems += "$($it.review_id): 存在しない指摘ID $($unknown -join ',')" }
  $known = @($srcs | Where-Object { $byId.ContainsKey($_) } | ForEach-Object { $byId[$_] })
  if (-not $known.Count -or -not $it.score) { continue }
  $checked++
  $expBase = ($known | Measure-Object base_score -Maximum).Maximum
  $expBonus = [math]::Min(1.5, 0.5 * (@($known | ForEach-Object { $_.persona_id } | Sort-Object -Unique).Count - 1))
  $s = $it.score; $adj = [double]$s.knowledge_adjust; $refs = @($s.knowledge_refs | Where-Object { $_ })
  if ([math]::Abs([double]$s.base_max - $expBase) -gt 0.05) { $problems += "$($it.review_id): base_max $($s.base_max)（再計算値 $expBase）" }
  if ([math]::Abs([double]$s.agreement_bonus - $expBonus) -gt 0.01) { $problems += "$($it.review_id): agreement_bonus $($s.agreement_bonus)（再計算値 $expBonus）" }
  if ($adj -lt -2 -or $adj -gt 2) { $problems += "$($it.review_id): knowledge_adjust $adj が範囲外" }
  if ($adj -ne 0 -and $refs.Count -eq 0) { $problems += "$($it.review_id): 知識補正に知識IDの引用なし" }
  $bad = @($refs | Where-Object { $kids -notcontains $_ }); if ($bad.Count) { $problems += "$($it.review_id): 存在しない知識ID $($bad -join ',')" }
  $expFinal = [math]::Round($expBase + $expBonus + $adj, 2)
  if ([math]::Abs([double]$s.final - $expFinal) -gt 0.05) { $problems += "$($it.review_id): final $($s.final)（再計算値 $expFinal）" }
}
[IO.File]::WriteAllText("$RunDir\verification.json", (ConvertTo-Json ([ordered]@{ items_checked=$checked; problems=$problems }) -Depth 4), $utf8)

# --- ペルソナ段階 ---
$hits = @{}; foreach ($f in $findings) { $hits[$f.finding_id] = @($key.defects | Where-Object { MatchesDefect $f $_ } | ForEach-Object { $_.id }) }
$detected = @{}; $classified = @{}; $detBy = @{}
foreach ($f in $findings) { foreach ($did in $hits[$f.finding_id]) {
  $detected[$did] = 1; if (-not $detBy[$did]) { $detBy[$did] = @() }; $detBy[$did] += $f.persona_id
  if (ClassOk $f.sub_characteristic $defById[$did]) { $classified[$did] = 1 } } }
$decoyP = @(); foreach ($f in $findings) { if (-not $hits[$f.finding_id].Count) { foreach ($dc in $key.decoys) { if (TextMatch $f.quote $dc.match_text) { $decoyP += "$($f.finding_id)→$($dc.id)" } } } }
$unmatchedP = @($findings | Where-Object { -not $hits[$_.finding_id].Count } | ForEach-Object { $_.finding_id })

# --- 最終段階 ---
$items = @($trace.items | Where-Object { @('採用', '将来の改善候補') -contains $_.status })
$fDet = @{}; $fCls = @{}; $fScore = @{}; $kOk = @{}; $fUnmatched = @(); $decoyF = @()
foreach ($it in $items) {
  $ih = @($it.source_findings | Where-Object { $byId.ContainsKey($_) } | ForEach-Object { $hits[$_] } | Sort-Object -Unique)
  if (-not $ih.Count) {
    $fUnmatched += $it.review_id
    foreach ($sid in $it.source_findings) { if ($byId.ContainsKey($sid)) { foreach ($dc in $key.decoys) { if (TextMatch $byId[$sid].quote $dc.match_text) { $decoyF += "$($it.review_id)→$($dc.id)" } } } }
  }
  foreach ($did in $ih) {
    $d = $defById[$did]; $fDet[$did] = 1
    if (ClassOk $it.sub_characteristic $d) { $fCls[$did] = 1 }
    $fs = [double]$it.score.final; if (-not $fScore.ContainsKey($did) -or $fs -gt $fScore[$did]) { $fScore[$did] = $fs }
    foreach ($r in @($it.score.knowledge_refs)) { if ($d.knowledge_refs -contains $r) { $kOk[$did] = 1 } }
  }
}
$withK = @($key.defects | Where-Object { $_.knowledge_refs.Count -and $fDet.ContainsKey($_.id) })

# 重要度の順位相関（スピアマン）
function Ranks([double[]]$xs) {
  $idx = 0..($xs.Count - 1) | Sort-Object { $xs[$_] }; $r = New-Object double[] $xs.Count; $i = 0
  while ($i -lt $idx.Count) { $j = $i; while ($j + 1 -lt $idx.Count -and $xs[$idx[$j + 1]] -eq $xs[$idx[$i]]) { $j++ }
    for ($k = $i; $k -le $j; $k++) { $r[$idx[$k]] = ($i + $j) / 2 + 1 }; $i = $j + 1 }
  return ,$r
}
$ids = @($fScore.Keys); $rho = '―'
if ($ids.Count -ge 3) {
  $ra = Ranks ([double[]]@($ids | ForEach-Object { $fScore[$_] })); $rb = Ranks ([double[]]@($ids | ForEach-Object { $defById[$_].expected_severity }))
  $ma = ($ra | Measure-Object -Average).Average; $mb = ($rb | Measure-Object -Average).Average
  $cov = 0; $va = 0; $vb = 0; for ($i = 0; $i -lt $ra.Count; $i++) { $cov += ($ra[$i] - $ma) * ($rb[$i] - $mb); $va += [math]::Pow($ra[$i] - $ma, 2); $vb += [math]::Pow($rb[$i] - $mb, 2) }
  if ($va -and $vb) { $rho = [math]::Round($cov / [math]::Sqrt($va * $vb), 3) }
}
function Pct($a, $b) { if ($b) { "{0}/{1}（{2:P0}）" -f $a, $b, ($a / $b) } else { '―' } }

$L = @("# 評価結果：$(Split-Path $RunDir -Leaf)", '', '## 全体', '', '| 指標 | ペルソナ段階（統合前） | 最終レビュー（統合後） |', '|---|---|---|',
  "| 指摘数 | $($findings.Count) | $($items.Count) |",
  "| 検出率 | $(Pct $detected.Count $n) | $(Pct $fDet.Count $n) |",
  "| 分類正解率 | $(Pct $classified.Count $detected.Count) | $(Pct $fCls.Count $fDet.Count) |",
  "| 誤検出（正しい記述への指摘） | $($decoyP.Count) | $($decoyF.Count) |",
  "| 対応なし（要目視確認） | $($unmatchedP.Count) | $($fUnmatched.Count) |",
  "| 重要度の順位相関 | ― | $rho |",
  "| 知識の反映率 | ― | $(Pct $kOk.Count $withK.Count) |",
  '', '## 副特性ごとの検出', '', '| 品質特性 | 副特性 | 欠陥数 | ペルソナ段階 | 最終 |', '|---|---|:-:|:-:|:-:|')
foreach ($g in ($key.defects | Group-Object sub_characteristic)) {
  $L += "| $($g.Group[0].characteristic) | $($g.Name) | $($g.Count) | $(@($g.Group | Where-Object { $detected.ContainsKey($_.id) }).Count) | $(@($g.Group | Where-Object { $fDet.ContainsKey($_.id) }).Count) |"
}
$L += '', '## ペルソナごと', '', '| ペルソナ | 指摘数 | 検出した欠陥数 | 独自に検出した欠陥 |', '|---|:-:|:-:|---|'
foreach ($pg in ($findings | Group-Object persona_id)) {
  $det = @($pg.Group | ForEach-Object { $hits[$_.finding_id] } | Sort-Object -Unique)
  $uniq = @($detBy.Keys | Where-Object { @($detBy[$_] | Sort-Object -Unique).Count -eq 1 -and $detBy[$_][0] -eq $pg.Name } | Sort-Object)
  $L += "| $($pg.Name) | $($pg.Count) | $($det.Count) | $(if ($uniq.Count) { $uniq -join ', ' } else { '―' }) |"
}
$missed = @($key.defects | Where-Object { -not $fDet.ContainsKey($_.id) })
$L += '', "## 最終レビューで見逃した欠陥（$($missed.Count)件）", ''
foreach ($d in $missed) { $L += "- $($d.id)（$($d.sub_characteristic)）$($d.description)$(if ($detected.ContainsKey($d.id)) { '　※ペルソナ段階では検出' })" }
$L += '', '## 誤検出', ''; $L += @($decoyP | ForEach-Object { "- ペルソナ段階 $_" }) + @($decoyF | ForEach-Object { "- 最終 $_" }); if (-not ($decoyP.Count + $decoyF.Count)) { $L += '- なし' }
$L += '', '## 目視確認が必要な項目（最終レビュー）', ''
foreach ($rid in $fUnmatched) { $it = $items | Where-Object { $_.review_id -eq $rid }; $L += "- $rid（$($it.sub_characteristic)）" }
if (-not $fUnmatched.Count) { $L += '- なし' }
$L += '', '## 判断の記録の再計算チェック', '', "確認 $checked 件、問題 $($problems.Count) 件", ''
$L += @($problems | ForEach-Object { "- $_" })
[IO.File]::WriteAllText("$RunDir\evaluation.md", ($L -join "`n") + "`n", $utf8)
$L -join "`n"
