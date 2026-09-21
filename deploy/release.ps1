[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Run this command from inside the FactorLab Git repository."
}

Push-Location $repoRoot
try {
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

    Invoke-Git -Arguments @("fetch", "--prune", "origin", "main:refs/remotes/origin/main")
    $head = (& git rev-parse "HEAD").Trim()
    $remoteHead = (& git rev-parse "refs/remotes/origin/main").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve HEAD and origin/main."
    }
    if ($head -ne $remoteHead) {
        throw "Local main must exactly match origin/main (unpushed, behind, and diverged states are refused)."
    }

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

    & git show-ref --verify --quiet "refs/tags/$tag"
    if ($LASTEXITCODE -eq 0) {
        throw "Release tag already exists locally: $tag"
    }
    if ($LASTEXITCODE -ne 1) {
        throw "Unable to check local tag state."
    }

    $remoteTag = & git ls-remote --tags origin "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to check whether the release tag exists on origin."
    }
    if ($remoteTag) {
        throw "Release tag already exists on origin: $tag"
    }

    Invoke-Git -Arguments @("tag", $tag)
    try {
        Invoke-Git -Arguments @("push", "origin", "refs/tags/$tag")
    }
    catch {
        & git tag --delete $tag | Out-Null
        throw
    }

    Write-Host "Release started: $tag"
    Write-Host "Commit: $head"
    Write-Host "GitHub Actions will test, publish, deploy, and verify this release."
}
finally {
    Pop-Location
}
