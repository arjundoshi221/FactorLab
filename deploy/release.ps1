<#
.SYNOPSIS
Start a FactorLab production release from a clean, synchronized main.

.DESCRIPTION
Without -Component: the legacy monolith release. Pushes release/<UTC>-<sha>, which
starts .github/workflows/release.yml (one image, every service).

With -Component <unit>: a per-unit release. tools/release.py writes the version and a
CHANGELOG.md section, the release commit is pushed to main, and an annotated
<unit>/vX.Y.Z tag starts .github/workflows/component-release.yml. A unit is a
component (components/<name>), platform, or a Cloudflare Worker (cloudflare/<name>).

.EXAMPLE
.\deploy\release.ps1 -Component api -Bump minor -DryRun
.\deploy\release.ps1 -Component api -Bump minor
.\deploy\release.ps1 -Component web              # release the declared version as-is
#>
[CmdletBinding(DefaultParameterSetName = "Monolith")]
param(
    [Parameter(ParameterSetName = "Component", Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{1,40}$')]
    [string]$Component,

    [Parameter(ParameterSetName = "Component")]
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump,

    [Parameter(ParameterSetName = "Component")]
    [switch]$DryRun,

    # Skip the "ci.yml is green for HEAD" check (for example when the GitHub CLI is unavailable).
    [Parameter(ParameterSetName = "Component")]
    [switch]$SkipCiCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Assert-SyncedMain {
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Releases must be created from the main branch; current branch is '$branch'."
    }

    $dirty = & git status --porcelain=v1 --untracked-files=all
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the worktree."
    }
    if ($dirty) {
        throw "The worktree is dirty. Commit or remove all tracked and untracked changes first."
    }

    # Fetch the branch tip into FETCH_HEAD. Combining --prune with an explicit
    # remote-tracking refspec can delete origin/main before it is resolved.
    Invoke-Git -Arguments @("fetch", "--no-tags", "origin", "main")
    $head = (& git rev-parse "HEAD").Trim()
    $remoteHead = (& git rev-parse "FETCH_HEAD").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve HEAD and the fetched origin/main."
    }
    if ($head -ne $remoteHead) {
        throw "Local main must exactly match origin/main (unpushed, behind, and diverged states are refused)."
    }
    return $head
}

function Assert-TagIsNew {
    param([Parameter(Mandatory)][string]$Tag)

    & git show-ref --verify --quiet "refs/tags/$Tag"
    if ($LASTEXITCODE -eq 0) {
        throw "Release tag already exists locally: $Tag"
    }
    if ($LASTEXITCODE -ne 1) {
        throw "Unable to check local tag state."
    }

    $remoteTag = & git ls-remote --tags origin "refs/tags/$Tag"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to check whether the release tag exists on origin."
    }
    if ($remoteTag) {
        throw "Release tag already exists on origin: $Tag"
    }
}

function Push-Tag {
    param([Parameter(Mandatory)][string]$Tag, [string]$Message)

    if ($Message) {
        Invoke-Git -Arguments @("tag", "--annotate", $Tag, "--message", $Message)
    }
    else {
        Invoke-Git -Arguments @("tag", $Tag)
    }
    try {
        Invoke-Git -Arguments @("push", "origin", "refs/tags/$Tag")
    }
    catch {
        & git tag --delete $Tag | Out-Null
        throw
    }
}

function Start-MonolithRelease {
    $head = Assert-SyncedMain

    # This is deliberately retained even after the equality check: it closes the race
    # between fetch and tagging if origin/main changes during a release.
    Invoke-Git -Arguments @("push", "origin", "main")

    $shortSha = (& git rev-parse --short=12 "HEAD").Trim()
    if ($LASTEXITCODE -ne 0 -or $shortSha -notmatch '^[0-9a-f]{7,12}$') {
        throw "Git returned an invalid short commit SHA: '$shortSha'."
    }

    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'")
    $tag = "release/$timestamp-$shortSha"
    if ($tag -notmatch '^release/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,12}$') {
        throw "Generated release tag does not satisfy the release contract: '$tag'."
    }

    Assert-TagIsNew -Tag $tag
    Push-Tag -Tag $tag

    Write-Host "Release started: $tag"
    Write-Host "Commit: $head"
    Write-Host "GitHub Actions will test, publish, deploy, and verify this release."
}

function Assert-CiGreen {
    param([Parameter(Mandatory)][string]$Commit)

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "The GitHub CLI (gh) is needed to confirm ci.yml passed for $Commit; install it or pass -SkipCiCheck."
    }
    $runs = & gh run list --workflow ci.yml --commit $Commit --json status,conclusion --limit 1
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read CI runs for $Commit."
    }
    $run = @($runs | ConvertFrom-Json)
    if ($run.Count -eq 0) {
        throw "ci.yml has not run for $Commit yet."
    }
    if ($run[0].status -ne "completed" -or $run[0].conclusion -ne "success") {
        throw "ci.yml for $Commit is '$($run[0].status)/$($run[0].conclusion)'; releases need a green run."
    }
}

function Start-UnitRelease {
    # A dry run writes nothing, so it may run anywhere (for example on a feature branch).
    if (-not $DryRun) {
        $head = Assert-SyncedMain
        if (-not $SkipCiCheck) {
            Assert-CiGreen -Commit $head
        }
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is needed to prepare the release (https://docs.astral.sh/uv/)."
    }

    $prepare = @("run", "--frozen", "python", "tools/release.py", "prepare", $Component)
    if ($Bump) { $prepare += @("--bump", $Bump) }
    if ($DryRun) { $prepare += "--dry-run" }
    $output = & uv @prepare
    if ($LASTEXITCODE -ne 0) {
        throw "tools/release.py prepare $Component failed; nothing was changed."
    }
    $plan = ($output -join "`n") | ConvertFrom-Json
    $previous = if ($plan.previous) { $plan.previous } else { "none" }

    Write-Host "$($plan.unit) $($plan.version) ($($plan.kind)); previous release: $previous"
    Write-Host ""
    Write-Host $plan.changelog
    if ($DryRun) {
        Write-Host "Dry run: nothing was written, committed, or tagged."
        return
    }

    Assert-TagIsNew -Tag $plan.tag
    Invoke-Git -Arguments (@("add", "--") + @($plan.files))
    Invoke-Git -Arguments @("commit", "--quiet", "--message", "chore(release): $($plan.unit) v$($plan.version)")
    try {
        Invoke-Git -Arguments @("push", "origin", "main")
    }
    catch {
        throw "Pushing the release commit failed (origin/main moved?). It exists only locally; inspect it, then drop it with 'git reset --hard origin/main'."
    }
    Push-Tag -Tag $plan.tag -Message "$($plan.unit) v$($plan.version)"

    Write-Host "Release started: $($plan.tag)"
    Write-Host "GitHub Actions (component-release.yml) will test, publish, deploy, and verify it."
}

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Run this command from inside the FactorLab Git repository."
}

Push-Location $repoRoot
try {
    if ($PSCmdlet.ParameterSetName -eq "Component") {
        Start-UnitRelease
    }
    else {
        Start-MonolithRelease
    }
}
finally {
    Pop-Location
}
