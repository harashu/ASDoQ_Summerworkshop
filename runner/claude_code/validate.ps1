param([string]$RunDir, [string]$DocPath, [string]$PersonaDir)
# review.py の [3] 検証・基礎スコア計算を PowerShell で再現する（Pythonがない環境での試行用）

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
$excluded = New-Object System.Collections.ArrayList
$goods = New-Object System.Collections.ArrayList

foreach ($raw in Get-ChildItem "$RunDir\raw\*.txt") {
  $pid_ = $raw.BaseName
  $pfile = Get-ChildItem "$PersonaDir\$pid_*.md" | Select-Object -First 1
  $fm = ([IO.File]::ReadAllText($pfile.FullName, $utf8) -split '(?m)^---\r?$')[1]
  $name = [regex]::Match($fm, '(?m)^name:\s*(.+)$').Groups[1].Value.Trim()
  $rel = [double][regex]::Match($fm, '(?m)^base_reliability:\s*([\d.]+)').Groups[1].Value
  $bias = [double]([regex]::Match($fm, '(?m)^severity_bias:\s*([+\-]?[\d.]+)').Groups[1].Value)
  $focus = @{}
  $focusLine = [regex]::Match($fm, '(?m)^focus:\s*\{(.+)\}').Groups[1].Value
  foreach ($m in [regex]::Matches($focusLine, '([^\s{},:]+):\s*([\d.]+)')) { $focus[$m.Groups[1].Value] = [double]$m.Groups[2].Value }

  $text = [IO.File]::ReadAllText($raw.FullName, $utf8)
  $m = [regex]::Match($text, '(?s)<result>(.*?)</result>')
  if (-not $m.Success) { [void]$excluded.Add([ordered]@{ persona_id=$pid_; reason='<result> がありません' }); continue }
  $json = ($m.Groups[1].Value.Trim() -replace '^```(json)?\s*', '' -replace '\s*```$', '')
  try { $data = $json | ConvertFrom-Json } catch { [void]$excluded.Add([ordered]@{ persona_id=$pid_; reason='JSONとして読み込めません' }); continue }

  $items = @()
  $i = 0; foreach ($x in @($data.findings)) { if ($x) { $i++; $items += ,@("$pid_-$i", $false, $x) } }
  $i = 0; foreach ($x in @($data.minor_findings)) { if ($x) { $i++; $items += ,@("$pid_-m$i", $true, $x) } }
  foreach ($entry in $items) {
    $fid = $entry[0]; $isMinor = $entry[1]; $f = $entry[2]
    $parts = @($f.sub_characteristic -split '[／/]' | Where-Object { $_.Trim() })
    # 公式名称にならない候補が1つでもあれば除外する（$null を含む配列の判定は PowerShell では偽になるため、件数で判定する）
    $invalid = @($parts | Where-Object { -not (Normalize-Sub $_) })
    $subs = @($parts | ForEach-Object { Normalize-Sub $_ } | Where-Object { $_ })
    if ($parts.Count -eq 0 -or $invalid.Count -gt 0) {
      [void]$excluded.Add([ordered]@{ finding_id=$fid; persona_id=$pid_; error='enum'; reason="副特性が公式名称ではありません: $($f.sub_characteristic)" }); continue
    }
    $ch = $f.characteristic; $autoFixed = $false
    $parents = @($subs | ForEach-Object { $parent[$_] })
    if ($parents -notcontains $ch) { $ch = $parent[$subs[0]]; $autoFixed = $true }

    $quote = if ($f.quote) { $f.quote.Trim() } else { '' }
    if ($quote) {
      $q = $quote.Trim('「', '」', '『', '』', '"', '“', '”')
      $qparts = @($q -split '…+|\.\.\.+' | ForEach-Object { Norm $_ } | Where-Object { $_ })
      $ok = $qparts.Count -gt 0
      foreach ($p in $qparts) { if (-not $doc.Contains($p)) { $ok = $false } }
      if (-not $ok) { [void]$excluded.Add([ordered]@{ finding_id=$fid; persona_id=$pid_; error='quote'; reason="引用が文書中に見つかりません: $quote" }); continue }
    }
    $sev = [math]::Min(5, [math]::Max(1, [int][math]::Round([double]$f.severity)))
    $conf = [math]::Min(1.0, [math]::Max(0.0, [double]$f.confidence))
    $adj = [math]::Min([double]5.0, [math]::Max([double]1.0, [double]$sev - $bias))
    $factor = if ($focus[$ch] -ge 0.2) { 1.0 } else { 0.7 }
    $score = [math]::Round($adj * $rel * $factor * $conf, 2)

    [void]$findings.Add([ordered]@{
      finding_id=$fid; persona_id=$pid_; persona_name=$name; characteristic=$ch; sub_characteristic=($subs -join '／');
      measurement_item=$f.measurement_item; location=$f.location; quote=$quote; raw_comment=$f.raw_comment; issue=$f.issue;
      suggestion=$f.suggestion; severity=$sev; confidence=$conf; auto_fixed=$autoFixed; is_minor=$isMinor; base_score=$score
    })
  }
  foreach ($g in $data.good_points) { [void]$goods.Add([ordered]@{ persona_id=$pid_; location=$g.location; sub_characteristic=$g.sub_characteristic; raw_comment=$g.raw_comment }) }
}

[IO.File]::WriteAllText("$RunDir\findings.json", (ConvertTo-Json @($findings) -Depth 5), $utf8)
[IO.File]::WriteAllText("$RunDir\excluded.json", (ConvertTo-Json @($excluded) -Depth 5), $utf8)
[IO.File]::WriteAllText("$RunDir\good_points.json", (ConvertTo-Json @($goods) -Depth 5), $utf8)
"採用: $($findings.Count) 件 / 除外: $($excluded.Count) 件 / 良い点: $($goods.Count) 件"
$findings | Group-Object persona_id | ForEach-Object { "  $($_.Name): $($_.Count) 件" }
$excluded | ForEach-Object { "  除外 $($_.finding_id): $($_.reason)" }
