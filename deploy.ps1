# Deploys the romvault folder straight to the LXC container over SSH,
# then syncs it into /opt/romvault (where the systemd service actually
# runs from) and restarts the service.
#
# Usage:
#   .\deploy.ps1 -ContainerIp 192.168.1.50
#
# First-time setup: edit $DefaultContainerIp below to your container's
# actual IP so you can just run ".\deploy.ps1" with no arguments after that.
#
# Requires: the container's SSH server reachable from this machine, and
# root SSH access to it (same password you use for pct enter, or a key).

param(
    [string]$ContainerIp = "",
    [string]$LocalPath = "$PSScriptRoot"
)

$DefaultContainerIp = "10.0.1.122"   # <-- set this once to your container's IP

if ([string]::IsNullOrWhiteSpace($ContainerIp)) {
    $ContainerIp = $DefaultContainerIp
}

if ($ContainerIp -eq "CHANGE-ME") {
    Write-Host "Set your container's IP first -- either edit `$DefaultContainerIp in this script," -ForegroundColor Yellow
    Write-Host "or run: .\deploy.ps1 -ContainerIp <your-container-ip>" -ForegroundColor Yellow
    exit 1
}

# Stage to a throwaway folder on the container first, then sync from
# there into /opt/romvault (the folder the systemd service actually
# runs from -- venv/ and instance/ live there and must NOT be clobbered).
Write-Host ">> Copying $LocalPath to root@${ContainerIp}:/root/romvault-staging ..."
ssh "root@${ContainerIp}" "rm -rf /root/romvault-staging"
scp -r $LocalPath "root@${ContainerIp}:/root/romvault-staging"
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! scp failed -- check the IP, network, and SSH credentials." -ForegroundColor Red
    exit 1
}

Write-Host ">> Syncing into /opt/romvault and restarting the service ..."
ssh "root@${ContainerIp}" "cp -r /root/romvault-staging/. /opt/romvault/ && chown -R romvault:romvault /opt/romvault && systemctl restart romvault && systemctl is-active romvault"
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! Sync/restart failed -- SSH in manually and check 'systemctl status romvault'." -ForegroundColor Red
    exit 1
}

Write-Host ">> Done. Refresh the ROM Vault page in your browser." -ForegroundColor Green
