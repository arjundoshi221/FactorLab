<#
.SYNOPSIS
Tag a CI-green commit on the dedicated UI deployment branch.

.DESCRIPTION
The immutable ui-trial/<short SHA> tag starts branch-ui-deploy.yml. That workflow
builds verified API and web images, then updates only the current branch-trial
api-candidate and web containers. It does not change main or ingestion services.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) { throw "Run from a Git worktree." }
Push-Location $repoRoot
try {
    $branch = (& git branch --show-current).Trim()
    if ($branch -ne "restructure/deploy-market-data-ui") {
        throw "The UI trial must start from restructure/deploy-market-data-ui."
    }
    $dirty = & git status --porcelain=v1 --untracked-files=all
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw "The UI trial worktree must be clean." }
    & git fetch --no-tags origin restructure/deploy-market-data-ui
    if ($LASTEXITCODE -ne 0) { throw "Could not fetch the deployment branch." }
    $commit = (& git rev-parse HEAD).Trim()
    $remoteHead = (& git rev-parse FETCH_HEAD).Trim()
    if ($commit -ne $remoteHead) { throw "The branch must match origin exactly." }
    $headers = @{ "User-Agent" = "FactorLab-UI-Trial" }
    $url = "https://api.github.com/repos/arjundoshi221/FactorLab/actions/runs?head_sha=$commit&per_page=30"
    $runs = Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 20
    $green = @($runs.workflow_runs | Where-Object {
        $_.name -eq "CI" -and $_.head_sha -eq $commit -and
        $_.status -eq "completed" -and $_.conclusion -eq "success"
    })
    if ($green.Count -eq 0) { throw "CI has not passed for $commit." }
    $short = $commit.Substring(0, 12)
    $tag = "ui-trial/$short"
    & git show-ref --verify --quiet "refs/tags/$tag"
    if ($LASTEXITCODE -eq 0) { throw "Trial tag already exists locally: $tag" }
    if ($LASTEXITCODE -ne 1) { throw "Could not check local tags." }
    $remoteTag = & git ls-remote --tags origin "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0 -or $remoteTag) { throw "Trial tag exists remotely or tag lookup failed." }
    & git tag --annotate $tag --message "UI trial $short"
    if ($LASTEXITCODE -ne 0) { throw "Could not create trial tag." }
    & git push origin "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) {
        & git tag --delete $tag | Out-Null
        throw "Trial tag push failed."
    }
    Write-Host "UI trial started: $tag"
    Write-Host "Commit: $commit"
    Write-Host "Follow branch-ui-deploy.yml through verification and recovery."
}
finally {
    Pop-Location
}
