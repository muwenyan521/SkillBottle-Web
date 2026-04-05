$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

$appsDir = Join-Path $projectRoot "app"
$frontendDir = Join-Path $projectRoot "frontend"
$resultRoot = Join-Path $projectRoot "result"

New-Item -ItemType Directory -Force $resultRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $resultRoot ("export-" + $stamp)
$outApps = Join-Path $outDir "apps"
New-Item -ItemType Directory -Force $outApps | Out-Null

function Get-AppItems {
  $items = @()
  if (-not (Test-Path $appsDir)) { return @() }

  Get-ChildItem -Path $appsDir -Directory | ForEach-Object {
    $index = Join-Path $_.FullName "index.html"
    if (Test-Path $index) {
      $name = $_.Name
      $items += [pscustomobject]@{ id = $name; label = $name; href = ("apps/" + $name + "/index.html") }
      Copy-Item -Recurse -Force $_.FullName (Join-Path $outApps $name)
    }
  }

  return @($items | Sort-Object label)
}

$items = @(Get-AppItems)
$itemsJson = if ($items.Count -eq 1) { "[" + ($items[0] | ConvertTo-Json -Depth 10) + "]" } else { ($items | ConvertTo-Json -Depth 10) }
$manifestJson = "{`n  ""items"": " + $itemsJson + "`n}`n"

$manifestPath = Join-Path $outDir "manifest.json"
$manifestJson | Set-Content -Encoding utf8 $manifestPath

$indexHtml = Get-Content (Join-Path $frontendDir "index.html") -Raw -Encoding utf8
$indexHtml = $indexHtml.Replace('href="/styles.css"', 'href="./styles.css"')
$indexHtml = $indexHtml.Replace('src="/app.js"', 'src="./app.js"')
$indexHtml = $indexHtml -replace 'href="/', 'href="./'
$indexHtml = $indexHtml -replace 'src="/', 'src="./'

$embed = "`n    <script type=""application/json"" id=""sb-manifest"">" + $manifestJson + "</script>`n"
$indexHtml = $indexHtml.Replace("`n    <script src=""./app.js""></script>", $embed + "    <script src=""./app.js""></script>")

Set-Content -Encoding utf8 (Join-Path $outDir "index.html") $indexHtml
Copy-Item -Force (Join-Path $frontendDir "styles.css") (Join-Path $outDir "styles.css")
Copy-Item -Force (Join-Path $frontendDir "app.js") (Join-Path $outDir "app.js")

Write-Host ("Export complete: " + $outDir) -ForegroundColor Green

