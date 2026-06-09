param(
    [string] $AppVersion = "",
    [string] $InnoSetupCompiler = ""
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}

function Read-ProjectVersion {
    param([Parameter(Mandatory = $true)][string] $RepoRoot)

    $pyproject = Join-Path $RepoRoot "pyproject.toml"
    $match = Select-String -LiteralPath $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($null -eq $match) {
        throw "Could not read project version from $pyproject"
    }
    return $match.Matches[0].Groups[1].Value
}

function Find-Iscc {
    param([string] $ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (Test-Path -LiteralPath $ExplicitPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $ExplicitPath).Path
        }
        throw "Inno Setup compiler not found at $ExplicitPath"
    }

    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw "ISCC.exe was not found. Install Inno Setup 6 or pass -InnoSetupCompiler <path>."
}

$repoRoot = Resolve-RepoRoot
if ([string]::IsNullOrWhiteSpace($AppVersion)) {
    $AppVersion = Read-ProjectVersion -RepoRoot $repoRoot
}

$buildRoot = Join-Path $repoRoot "build\windows"
$stageRoot = Join-Path $buildRoot "stage"
$appStage = Join-Path $stageRoot "app"
$pyInstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$pyInstallerWork = Join-Path $buildRoot "pyinstaller-work"
$distRoot = Join-Path $repoRoot "dist\windows"
$specPath = Join-Path $PSScriptRoot "renderdoc-mcp.spec"
$issPath = Join-Path $PSScriptRoot "RenderDocMCP.iss"

Write-Host "=== RenderDoc MCP Windows build ==="
Write-Host "Repo      : $repoRoot"
Write-Host "Version   : $AppVersion"
Write-Host "Build root: $buildRoot"
Write-Host ""

if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $appStage -Force | Out-Null
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

$uv = Get-Command "uv.exe" -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv.exe was not found. Install uv first: https://docs.astral.sh/uv/"
}

Push-Location $repoRoot
try {
    Write-Host "=== [1/4] Run tests ==="
    & $uv.Source run pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed"
    }

    Write-Host ""
    Write-Host "=== [2/4] Build PyInstaller onedir app ==="
    & $uv.Source run --with pyinstaller pyinstaller --noconfirm --clean $specPath --distpath $pyInstallerDist --workpath $pyInstallerWork
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed"
    }

    $builtApp = Join-Path $pyInstallerDist "renderdoc-mcp"
    if (-not (Test-Path -LiteralPath (Join-Path $builtApp "renderdoc-mcp.exe") -PathType Leaf)) {
        throw "PyInstaller did not produce renderdoc-mcp.exe in $builtApp"
    }

    Get-ChildItem -LiteralPath $builtApp -Force |
        Copy-Item -Destination $appStage -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "renderdoc_extension") -Destination $stageRoot -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_renderdoc_extension.ps1") -Destination (Join-Path $stageRoot "install_renderdoc_extension.ps1") -Force

    Write-Host ""
    Write-Host "=== [3/4] Build Inno Setup installer ==="
    $iscc = Find-Iscc -ExplicitPath $InnoSetupCompiler
    & $iscc "/DAppVersion=$AppVersion" "/DSourceDir=$stageRoot" $issPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed"
    }

    $setupExe = Join-Path $distRoot "RenderDocMCP-Setup-$AppVersion.exe"
    if (-not (Test-Path -LiteralPath $setupExe -PathType Leaf)) {
        throw "Installer was not created: $setupExe"
    }

    Write-Host ""
    Write-Host "=== [4/4] Done ==="
    Write-Host "Installer: $setupExe"
} finally {
    Pop-Location
}
