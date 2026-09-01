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

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$meshPath = "/mnt/c/GH/voxelate/$OutDir/mesh.glb"
$escapedText = $Text -replace "'", "'\''"

Write-Host "==> Generating mesh from text via TRELLIS (WSL2)..."
wsl.exe -d $WslDistro -e bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate trellis && cd ~/TRELLIS && python generate.py '$escapedText' -o $meshPath --seed $Seed --steps $Steps --texture-size $TextureSize --simplify $Simplify"
if ($LASTEXITCODE -ne 0) { throw "TRELLIS generation failed (exit code $LASTEXITCODE)" }

Write-Host "==> Voxelizing mesh..."
uv run python voxelate.py "$OutDir/mesh.glb" -o "$OutDir/voxels.ply" -p $Pitch
if ($LASTEXITCODE -ne 0) { throw "Voxelization failed (exit code $LASTEXITCODE)" }

Write-Host "==> Done: $OutDir/voxels.ply"
