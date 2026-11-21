[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy/.env.cloud.psd1'),
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Copy deploy/cloud.psd1.example to '$ConfigPath' and fill in the deployment settings."
}
$configFile = Get-Item -LiteralPath $ConfigPath
$config = Import-PowerShellDataFile -LiteralPath $configFile.FullName
$managerHost = [string]$config.ManagerHost
$managerUser = [string]$config.ManagerUser
$imageTag = [string]$config.ImageTag
if ($managerHost -cnotmatch '\A(?:[A-Za-z0-9][A-Za-z0-9.-]*|[A-Fa-f0-9]*:[A-Fa-f0-9:]+)\z') {
    throw 'ManagerHost must be the hostname or IP address of the Swarm manager.'
}
if ($managerUser -cnotmatch '\A[A-Za-z_][A-Za-z0-9_.-]*\z') {
    throw 'ManagerUser must be the SSH login name on the Swarm manager.'
}
if ($imageTag -cnotmatch '\A[a-f0-9]{40}\z') {
    throw 'ImageTag must be the full lowercase commit SHA published by the Release workflow.'
}
$managerPort = 22
if ($config.ContainsKey('ManagerPort') -and
    (-not [int]::TryParse([string]$config.ManagerPort, [ref]$managerPort) -or
        $managerPort -lt 1 -or $managerPort -gt 65535)) {
    throw 'ManagerPort must be an integer between 1 and 65535.'
}

function Get-SshFilePath([string]$Value, [string]$Setting) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Setting must name an existing file."
    }
    if (-not [IO.Path]::IsPathRooted($Value)) {
        $Value = Join-Path $configFile.DirectoryName $Value
    }
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) {
        throw "$Setting file does not exist: $Value"
    }
    $filePath = (Get-Item -LiteralPath $Value).FullName
    if ($filePath -match '["\r\n]') {
        throw "$Setting path contains a character unsupported by the SSH configuration."
    }
    return $filePath.Replace('\', '/').Replace('%', '%%')
}

$knownHosts = Get-SshFilePath $config.KnownHostsFile 'KnownHostsFile'
$sshConfig = @(
    'Host fastzip-deploy'
    "    HostName $managerHost"
    "    User $managerUser"
    "    Port $managerPort"
    "    UserKnownHostsFile `"$knownHosts`""
    '    GlobalKnownHostsFile none'
    '    StrictHostKeyChecking yes'
    '    UpdateHostKeys no'
    '    BatchMode yes'
    '    ConnectTimeout 15'
)
if ($config.IdentityFile) {
    $identityFile = Get-SshFilePath $config.IdentityFile 'IdentityFile'
    $sshConfig += "    IdentityFile `"$identityFile`"", '    IdentitiesOnly yes'
}

$gitCommand = (Get-Command git -CommandType Application | Select-Object -First 1).Source
$sshCommand = (Get-Command ssh -CommandType Application | Select-Object -First 1).Source
$commit = & $gitCommand -C $repoRoot rev-parse --verify "${imageTag}^{commit}"
if ($LASTEXITCODE -ne 0 -or $commit -cne $imageTag) {
    throw 'The image commit must be available in this checkout. Fetch the Release workflow commit first.'
}
$entries = & $gitCommand -C $repoRoot ls-tree -r --full-tree $imageTag -- deploy scripts
if ($LASTEXITCODE -ne 0 -or ($entries -match '^(120000|160000) ')) {
    throw 'The release tree could not be read or contains a symbolic link or submodule in deploy/ or scripts/.'
}
$releasePaths = $entries | ForEach-Object { ($_ -split "`t", 2)[1] }
foreach ($required in @('deploy/bootstrap.yaml', 'deploy/stack.yaml', 'scripts/deploy.sh', 'scripts/wait_stack.py')) {
    if ($releasePaths -cnotcontains $required) {
        throw "File missing from the commit: $required"
    }
}

function Invoke-ManagerScript([string]$Script, [string[]]$Arguments = @()) {
    # Base64 preserves Unix line endings across Windows PowerShell's native argument handling.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Script -replace "`r`n", "`n")))
    foreach ($argument in $Arguments) {
        if ($argument -cnotmatch '\A[A-Za-z0-9/._-]+\z') { throw 'Invalid deployment argument.' }
    }
    $remoteCommand = "printf %s '$encoded' | base64 -d | bash -se -- $($Arguments -join ' ')"
    & $sshCommand -F $sshConfigPath fastzip-deploy $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "Swarm manager command failed with exit code $LASTEXITCODE." }
}

$managerCheck = @'
set -euo pipefail
fail() { printf '%s\n' "$*" >&2; exit 1; }
for command in docker python3 flock findmnt tar diff sha256sum; do
  command -v "$command" >/dev/null || fail "Required on the manager: $command"
done
[[ -w /opt/fastzip && -r /opt/fastzip/production.env ]] || fail 'Set up /opt/fastzip/production.env and give the SSH user write access to /opt/fastzip.'
source /opt/fastzip/production.env
for setting in REGISTRY DOMAIN SPACES_ENDPOINT SPACES_REGION MEDIA_BUCKET BACKUP_BUCKET BACKUP_AGE_RECIPIENT POSTGRES_VOLUME_PATH DO_DROPLET_IDS; do
  [[ -n "${!setting:-}" && "${!setting}" != REPLACE* ]] || fail "Fill in $setting in /opt/fastzip/production.env."
done
[[ "$BACKUP_BUCKET" != "$MEDIA_BUCKET" ]] || fail 'Use separate media and backup buckets.'
[[ "$BACKUP_AGE_RECIPIENT" == age1* ]] || fail 'Set BACKUP_AGE_RECIPIENT to the public age recipient.'
[[ "$POSTGRES_VOLUME_PATH" == /* && -d "$POSTGRES_VOLUME_PATH" ]] || fail 'Create POSTGRES_VOLUME_PATH on the mounted PostgreSQL volume first.'
volume_mount=$(findmnt -rn -o TARGET --target "$POSTGRES_VOLUME_PATH")
[[ -n "$volume_mount" && "$volume_mount" != / ]] || fail 'POSTGRES_VOLUME_PATH currently resolves to the root filesystem. Mount the PostgreSQL volume first.'
if existing_path=$(docker volume inspect fastzip_database --format '{{index .Options "device"}}' 2>/dev/null); then
  [[ "$existing_path" == "$POSTGRES_VOLUME_PATH" ]] || fail 'The existing fastzip_database volume uses a different source path.'
fi
manager_id=$(docker info --format '{{.Swarm.NodeID}}')
node_ids=$(docker node ls --quiet) || fail 'SSH user must have Docker access on the Swarm manager.'
mapfile -t node_ids <<< "$node_ids"
nodes=$(docker node inspect --format '{{.ID}}|{{.Spec.Role}}|{{.Status.State}}|{{.Spec.Availability}}|{{index .Spec.Labels "fastzip.app"}}|{{index .Spec.Labels "fastzip.db"}}' "${node_ids[@]}")
manager_ready=false
worker_ready=false
while IFS='|' read -r id role state availability app database; do
  if [[ "$id" == "$manager_id" && "$role" == manager && "$state" == ready && "$availability" == active && "$app" == true && "$database" == true ]]; then
    manager_ready=true
  fi
  if [[ "$id" != "$manager_id" && "$database" == true ]]; then
    fail 'Keep the fastzip.db label on the manager with the PostgreSQL volume.'
  fi
  if [[ "$role" == worker && "$state" == ready && "$availability" == active && "$app" == true ]]; then
    worker_ready=true
  fi
done <<< "$nodes"
[[ "$manager_ready" == true ]] || fail 'The manager must be Ready and Active with fastzip.app=true and fastzip.db=true.'
[[ "$worker_ready" == true ]] || fail 'A second Swarm node must be Ready and Active with fastzip.app=true.'
docker secret inspect fastzip_db_password fastzip_session_secret fastzip_spaces_access fastzip_spaces_secret fastzip_backup_access fastzip_backup_secret fastzip_do_token >/dev/null
inspect=(docker manifest inspect)
if docker buildx version >/dev/null 2>&1; then
  inspect=(docker buildx imagetools inspect)
fi
for service in api worker backup frontend; do
  image="${REGISTRY}/${service}:$1"
  "${inspect[@]}" "$image" >/dev/null || fail "Unable to read $image. Publish this commit and confirm registry access on the manager."
done
'@

$managerDeploy = @'
set -euo pipefail
umask 077
stage=$1
tag=$2
digest=$3
release="/opt/fastzip/releases/$tag"
printf '%s  %s\n' "$digest" "$stage/release.tar.gz" | sha256sum -c --status
mkdir -p /opt/fastzip/releases "$stage/release"
tar --extract --gzip --file "$stage/release.tar.gz" --directory "$stage/release" --no-same-owner --no-same-permissions
# Preserve the prior copy of a release when another deployment uploaded the same commit.
if [[ -e "$release" || -L "$release" ]] || ! mv --no-target-directory "$stage/release" "$release"; then
  [[ -d "$release" && ! -L "$release" ]] || { echo 'The release path is not a regular directory.' >&2; exit 1; }
  diff -qr "$stage/release" "$release"
fi
cd "$release"
exec bash scripts/deploy.sh "$tag"
'@

$workingPath = Join-Path ([IO.Path]::GetTempPath()) ('fastzip-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workingPath | Out-Null
$sshConfigPath = Join-Path $workingPath 'ssh_config'
$archivePath = Join-Path $workingPath 'release.tar.gz'
$remoteStage = $null
try {
    [IO.File]::WriteAllLines($sshConfigPath, $sshConfig, [Text.UTF8Encoding]::new($false))
    Write-Host "Checking Swarm manager $managerUser@$managerHost for release $imageTag."
    Invoke-ManagerScript $managerCheck @($imageTag)
    if ($CheckOnly) {
        Write-Host 'Manager checks passed. No release was uploaded or deployed.'
        return
    }

    $scpCommand = (Get-Command scp -CommandType Application | Select-Object -First 1).Source
    & $gitCommand -C $repoRoot archive --format=tar.gz "--output=$archivePath" $imageTag -- deploy scripts
    if ($LASTEXITCODE -ne 0) { throw 'Could not archive the pinned release.' }
    $digest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $stage = Invoke-ManagerScript 'umask 077; mktemp -d /opt/fastzip/.upload.XXXXXXXX'
    if ($stage -isnot [string] -or $stage -cnotmatch '\A/opt/fastzip/\.upload\.[A-Za-z0-9]{8}\z') {
        throw 'The manager did not return a valid release staging location.'
    }
    $remoteStage = $stage
    & $scpCommand -F $sshConfigPath $archivePath "fastzip-deploy:$remoteStage/release.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "Release upload failed with exit code $LASTEXITCODE." }
    Invoke-ManagerScript $managerDeploy @($remoteStage, $imageTag, $digest)
} finally {
    try {
        if ($remoteStage) {
            Invoke-ManagerScript 'rm -rf -- "$1"' @($remoteStage)
        }
    } catch {
        Write-Warning "Could not clear the release staging directory on the manager: $remoteStage"
    } finally {
        Remove-Item -LiteralPath $sshConfigPath, $archivePath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $workingPath -ErrorAction SilentlyContinue
    }
}
