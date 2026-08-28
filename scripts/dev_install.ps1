<#
.SYNOPSIS
    Install this repository into QGIS for development, or remove it.

.DESCRIPTION
    Links `align_connector/` into the QGIS plugin folder, so edits in the
    repository are picked up by reloading the plugin -- no copying between
    iterations.

    !!  READ THIS ONCE. QGIS' "Uninstall" button DELETES the plugin folder, and
    it follows the junction: uninstalling from inside QGIS erases the source
    files in this repository. To turn the plugin off, UNCHECK it in
    Plugins -> Manage and Install Plugins -> Installed. To remove it for real,
    run this script with -Uninstall, which removes the link and never touches
    what it points at.

    (If it already happened: nothing is lost as long as the work was committed
    -- `git restore align_connector/` brings it back.)

.PARAMETER Uninstall
    Remove the link instead of creating it.

.PARAMETER Profile
    QGIS profile name. Defaults to "default".

.EXAMPLE
    pwsh scripts/dev_install.ps1
    pwsh scripts/dev_install.ps1 -Uninstall
#>
param(
    [switch]$Uninstall,
    [string]$Profile = 'default'
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'align_connector'
$pluginsDir = Join-Path $env:APPDATA "QGIS\QGIS3\profiles\$Profile\python\plugins"
$link = Join-Path $pluginsDir 'align_connector'

if ($Uninstall) {
    if (Test-Path $link) {
        # Remove the link itself. Deleting a junction with Remove-Item does not
        # recurse into the target -- unlike what QGIS' uninstall does.
        [System.IO.Directory]::Delete($link, $false)
        Write-Output "Removed the link. The repository is untouched."
    } else {
        Write-Output "Nothing to remove -- no link at $link"
    }
    return
}

if (-not (Test-Path $source)) {
    throw "align_connector/ is missing from $repo. If QGIS' Uninstall button erased it, run: git restore align_connector/"
}

New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null

if (Test-Path $link) {
    [System.IO.Directory]::Delete($link, $false)
}
New-Item -ItemType Junction -Path $link -Target $source | Out-Null

Write-Output "Linked $link -> $source"
Write-Output ""
Write-Output "In QGIS: Plugins -> Manage and Install Plugins -> Installed -> tick 'ALIGN Connector'."
Write-Output "To disable it later, UNCHECK it there. Never use 'Uninstall' -- it deletes the source."
