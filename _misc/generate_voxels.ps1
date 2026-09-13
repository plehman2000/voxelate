param(
    [Parameter(Mandatory=$true)][string]$Text,
    [string]$OutDir = "outputs",
    [double]$Pitch = 0.02,
    [int]$Seed = 1,
    [int]$Steps = 25,
    [int]$TextureSize = 512,
    [double]$Simplify = 0.97,
    [string]$WslDistro = "Ubuntu"
)

$ErrorActionPreference = "Stop"

$totalTimer = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$meshPath = "/mnt/c/GH/voxelate/$OutDir/mesh.glb"
$escapedText = $Text -replace "'", "'\''"

Write-Host "==> Generating mesh from text via TRELLIS (WSL2)..."
$stageTimer = [System.Diagnostics.Stopwatch]::StartNew()
wsl.exe -d $WslDistro -e bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate trellis && cd ~/TRELLIS && python generate.py '$escapedText' -o $meshPath --seed $Seed --steps $Steps --texture-size $TextureSize --simplify $Simplify"
$stageTimer.Stop()
if ($LASTEXITCODE -ne 0) { throw "TRELLIS generation failed (exit code $LASTEXITCODE)" }
Write-Host ("==> Mesh generated in {0:N2}s" -f $stageTimer.Elapsed.TotalSeconds)

Write-Host "==> Voxelizing mesh..."
$stageTimer.Restart()
uv run python voxelate.py "$OutDir/mesh.glb" -o "$OutDir/voxels.ply" -p $Pitch
$stageTimer.Stop()
if ($LASTEXITCODE -ne 0) { throw "Voxelization failed (exit code $LASTEXITCODE)" }
Write-Host ("==> Voxelized in {0:N2}s" -f $stageTimer.Elapsed.TotalSeconds)

$totalTimer.Stop()
Write-Host ("==> Done in {0:N2}s: $OutDir/voxels.ply" -f $totalTimer.Elapsed.TotalSeconds)
