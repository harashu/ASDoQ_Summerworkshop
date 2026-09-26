param([string]$RunDir, [string]$DocPath)
# review.py の [4] 判定役の結果の反映を PowerShell で再現する（APIキーなしの試行用）
# raw\judge_<特性名>.txt を読み、judgments.json を作り、判定役の追加指摘を検証して findings.json に加える。
# 分類の判定（再分類・移管など）はオーケストレーターが judgments.json を見て反映する。

$utf8 = New-Object System.Text.UTF8Encoding($false)
$parent = @{
  '目的明示性'='目的適合性'; '目的合致性'='目的適合性';
  '再利用性'='保守性'; '持続可能性'='保守性';
  '無矛盾性'='整合性'; '一貫性'='整合性'; '構造性'='整合性';
  '非曖昧性（一意性）'='理解容易性'; '非曖昧性（識別性）'='理解容易性'; '関係性'='理解容易性';
  '簡潔性'='可読性'; '表記適切性'='可読性'; '表現適切性'='可読性';
  '制約適合性'='規範適合性'; '基準適合性'='規範適合性'; '文法適合性'='規範適合性'; '記法適合性'='規範適合性'
}
function Normalize-Sub([string]$s) {
  if (-not $s) { return $null }
  $t = $s.Trim().Replace('(', '（').Replace(')', '）').Replace(' ', '')
  if ($t -eq '一意性') { $t = '非曖昧性（一意性）' }
  if ($t -eq '識別性') { $t = '非曖昧性（識別性）' }
  if ($parent.ContainsKey($t)) { return $t } else { return $null }
}
function Norm([string]$s) { if (-not $s) { return '' }; return ($s -replace '\s', '') }

$doc = Norm ([IO.File]::ReadAllText($DocPath, $utf8))
$findings = New-Object System.Collections.ArrayList
foreach ($f in (([IO.File]::ReadAllText("$RunDir\findings.json", $utf8) | ConvertFrom-Json) | ForEach-Object { $_ })) { [void]$findings.Add($f) }
$excluded = New-Object System.Collections.ArrayList
foreach ($e in (([IO.File]::ReadAllText("$RunDir\excluded.json", $utf8) | ConvertFrom-Json) | ForEach-Object { $_ })) { if ($e) { [void]$excluded.Add($e) } }

$judgments = [ordered]@{}
$added = 0
foreach ($file in Get-ChildItem "$RunDir\raw\judge_*.txt") {
  $ch = $file.BaseName.Substring(6)
  $text = [IO.File]::ReadAllText($file.FullName, $utf8)
  $m = [regex]::Match($text, '(?s)<result>(.*?)</result>')
  if (-not $m.Success) { $judgments[$ch] = @{ error = '<result> がありません' }; continue }
  try { $data = ($m.Groups[1].Value.Trim() -replace '^```(json)?\s*', '' -replace '\s*```$', '') | ConvertFrom-Json } catch { $judgments[$ch] = @{ error = 'JSONとして読み込めません' }; continue }
  $judgments[$ch] = $data

  $i = 0
  foreach ($a in @($data.additions)) {
    if (-not $a) { continue }
    $i++
    $fid = "J-$ch-$i"
    $parts = @($a.sub_characteristic -split '[／/]' | Where-Object { $_.Trim() })
    $invalid = @($parts | Where-Object { -not (Normalize-Sub $_) })
    $subs = @($parts | ForEach-Object { Normalize-Sub $_ } | Where-Object { $_ })
    if ($parts.Count -eq 0 -or $invalid.Count -gt 0) { [void]$excluded.Add([ordered]@{ finding_id=$fid; persona_id='J'; error='enum'; reason="副特性が公式名称ではありません: $($a.sub_characteristic)" }); continue }
    $quote = if ($a.quote) { $a.quote.Trim() } else { '' }
    $q = $quote.Trim('「', '」', '『', '』', '"', '“', '”')
    $qparts = @($q -split '…+|\.\.\.+' | ForEach-Object { Norm $_ } | Where-Object { $_ })
    $ok = $qparts.Count -gt 0
    foreach ($p in $qparts) { if (-not $doc.Contains($p)) { $ok = $false } }
    if (-not $ok) { [void]$excluded.Add([ordered]@{ finding_id=$fid; persona_id='J'; error='quote'; reason="引用が文書中に見つかりません: $quote" }); continue }
    $sev = [math]::Min(5, [math]::Max(1, [int][math]::Round([double]$a.severity)))
    $conf = [math]::Min(1.0, [math]::Max(0.0, [double]$a.confidence))
    # 判定役の追加指摘：base_reliability 0.9、severity_bias 0、重点係数 1.0（docs/design.md 5章）
    $score = [math]::Round([double]$sev * 0.9 * 1.0 * $conf, 2)
    [void]$findings.Add([ordered]@{
      finding_id=$fid; persona_id='J'; persona_name='特性別の判定役'; characteristic=$parent[$subs[0]]; sub_characteristic=($subs -join '／');
      measurement_item=$a.measurement_item; location=$a.location; quote=$quote; raw_comment=$null; issue=$a.issue;
      suggestion=$null; severity=$sev; confidence=$conf; auto_fixed=$false; is_minor=$false; base_score=$score
    })
    $added++
  }
}

[IO.File]::WriteAllText("$RunDir\judgments.json", (ConvertTo-Json $judgments -Depth 8), $utf8)
[IO.File]::WriteAllText("$RunDir\findings.json", (ConvertTo-Json @($findings) -Depth 6), $utf8)
[IO.File]::WriteAllText("$RunDir\excluded.json", (ConvertTo-Json @($excluded) -Depth 6), $utf8)
"判定役 $($judgments.Count) 件の結果を反映 / 追加指摘 $added 件を採用 / 指摘の合計 $($findings.Count) 件"
foreach ($ch in $judgments.Keys) {
  $d = $judgments[$ch]
  $v = @($d.checks | Group-Object verdict | ForEach-Object { "$($_.Name) $($_.Count)" }) -join '、'
  "  $ch：判定 $v ／ 未確認の副特性 $(@($d.coverage | Where-Object { $_.status -eq '未確認' }).Count)"
}
$excluded | Where-Object { $_.persona_id -eq 'J' } | ForEach-Object { "  除外 $($_.finding_id): $($_.reason)" }
