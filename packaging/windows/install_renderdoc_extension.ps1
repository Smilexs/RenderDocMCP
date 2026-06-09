param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "uninstall")]
    [string] $Command = "install",

    [string] $ExtensionSource = "",

    [string] $ExtensionDir = "$env:APPDATA\qrenderdoc\extensions",

    [switch] $NoAlwaysLoad
)

$ErrorActionPreference = "Stop"

$ExtensionName = "renderdoc_mcp_bridge"
$AlwaysLoadKey = "AlwaysLoad_Extensions"

function Resolve-InstallPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $expanded))
}

function Read-UiConfig {
    param(
        [Parameter(Mandatory = $true)][string] $ConfigPath,
        [Parameter(Mandatory = $true)][bool] $CreateIfMissing
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        if ($CreateIfMissing) {
            return [pscustomobject]@{}
        }
        return $null
    }

    $raw = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{}
    }

    $data = $raw | ConvertFrom-Json
    if ($null -eq $data) {
        return [pscustomobject]@{}
    }
    return $data
}

function Write-UiConfig {
    param(
        [Parameter(Mandatory = $true)][string] $ConfigPath,
        [Parameter(Mandatory = $true)] $Data
    )

    $parent = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null

    $json = $Data | ConvertTo-Json -Depth 100
    $tmp = "$ConfigPath.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $json + "`n", $utf8NoBom)
    Move-Item -LiteralPath $tmp -Destination $ConfigPath -Force
}

function Get-AlwaysLoadEntries {
    param($Data)

    $property = $Data.PSObject.Properties[$AlwaysLoadKey]
    if ($null -eq $property -or $null -eq $property.Value) {
        return @()
    }
    if ($property.Value -is [System.Array]) {
        return @($property.Value)
    }
    return @($property.Value)
}

function Set-AlwaysLoadEntries {
    param(
        [Parameter(Mandatory = $true)] $Data,
        [object[]] $Entries = @()
    )

    $property = $Data.PSObject.Properties[$AlwaysLoadKey]
    if ($null -eq $property) {
        Add-Member -InputObject $Data -MemberType NoteProperty -Name $AlwaysLoadKey -Value $Entries
    } else {
        $property.Value = $Entries
    }
}

function Configure-AlwaysLoad {
    param(
        [Parameter(Mandatory = $true)][string] $ExtensionDirectory,
        [Parameter(Mandatory = $true)][bool] $Enabled
    )

    $configPath = Join-Path (Split-Path -Parent $ExtensionDirectory) "UI.config"
    $data = Read-UiConfig -ConfigPath $configPath -CreateIfMissing:$Enabled
    if ($null -eq $data) {
        Write-Host "[INFO] UI.config not found; no Always Load entry to remove: $configPath"
        return
    }

    $entries = @(Get-AlwaysLoadEntries -Data $data)
    if ($Enabled) {
        if ($entries -notcontains $ExtensionName) {
            $entries += $ExtensionName
            Set-AlwaysLoadEntries -Data $data -Entries $entries
            Write-UiConfig -ConfigPath $configPath -Data $data
            Write-Host "[OK] Enabled Always Load in $configPath"
        } else {
            Write-Host "[OK] Always Load already configured in $configPath"
        }
    } else {
        $filtered = @($entries | Where-Object { $_ -ne $ExtensionName })
        if ($filtered.Count -ne $entries.Count) {
            Set-AlwaysLoadEntries -Data $data -Entries $filtered
            Write-UiConfig -ConfigPath $configPath -Data $data
            Write-Host "[OK] Removed Always Load entry from $configPath"
        } else {
            Write-Host "[OK] Always Load entry already absent in $configPath"
        }
    }
}

function Copy-ExtensionTree {
    param(
        [Parameter(Mandatory = $true)][string] $Source,
        [Parameter(Mandatory = $true)][string] $Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Extension source directory not found: $Source"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Source "extension.json") -PathType Leaf)) {
        throw "extension.json not found in extension source: $Source"
    }

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        if ($_.Name -eq "__pycache__") {
            return
        }
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }

    Get-ChildItem -LiteralPath $Destination -Recurse -Directory -Force -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $Destination -Recurse -File -Force |
        Where-Object { $_.Extension -eq ".pyc" -or $_.Extension -eq ".pyo" } |
        Remove-Item -Force
}

$resolvedExtensionDir = Resolve-InstallPath -Path $ExtensionDir
$destination = Join-Path $resolvedExtensionDir $ExtensionName

if ($Command -eq "install") {
    if ([string]::IsNullOrWhiteSpace($ExtensionSource)) {
        throw "-ExtensionSource is required for install"
    }

    $resolvedSource = Resolve-InstallPath -Path $ExtensionSource
    Write-Host "[RUN] Installing RenderDoc MCP Bridge extension"
    Write-Host "      Source: $resolvedSource"
    Write-Host "      Target: $destination"

    Copy-ExtensionTree -Source $resolvedSource -Destination $destination
    if (-not $NoAlwaysLoad) {
        Configure-AlwaysLoad -ExtensionDirectory $resolvedExtensionDir -Enabled:$true
    }
    Write-Host "[OK] Extension installed"
} else {
    Write-Host "[RUN] Uninstalling RenderDoc MCP Bridge extension"
    Write-Host "      Target: $destination"

    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Recurse -Force
        Write-Host "[OK] Removed $destination"
    } else {
        Write-Host "[OK] Extension already absent: $destination"
    }
    if (-not $NoAlwaysLoad) {
        Configure-AlwaysLoad -ExtensionDirectory $resolvedExtensionDir -Enabled:$false
    }
    Write-Host "[OK] Extension uninstalled"
}
