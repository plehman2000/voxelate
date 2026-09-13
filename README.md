# voxelate

Archetype → LLM writes a small primitive spec → deterministic compiler → `VoxelObjectPlan` JSON (4x4x12 max) + PNG preview.

```powershell
uv run python voxgen.py outpost     # one archetype -> outputs/objects/outpost.{json,spec.json,png}
uv run python voxgen.py --all       # all 13 archetypes
```

Needs `OPENAI_API_KEY` in `.env`. Set `VOXGEN_PROVIDER=ollama` (+ `OLLAMA_MODEL`) to use a local model instead.

## How it works

Everything is in [voxgen.py](voxgen.py):

- **Spec** (what the LLM writes): `box`, `hollow_box`, `column`, `pyramid`, `erase` parts with a material.
- **Compiler**: applies parts in order, clamps to bounds, maps unknown materials to `stone`, keeps only the largest face-connected component (no floating voxels).
- **Output**: `{"brief_summary", "voxels": [{x,y,z,material}]}`.

The old TRELLIS text-to-mesh pipeline lives in [_misc/](_misc/).
