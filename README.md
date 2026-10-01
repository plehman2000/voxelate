# voxelate

Archetype → LLM writes a small primitive spec → deterministic compiler → `VoxelObjectPlan` JSON (12x12x20 max) + PNG preview.

```powershell
uv run python voxgen.py outpost     # one archetype -> outputs/objects/outpost.{json,spec.json,png}
uv run python voxgen.py --all       # all 13 archetypes
```

## Commands

```powershell
# generate
uv run python voxgen.py outpost                         # one archetype
uv run python voxgen.py --all                           # all 13 archetypes
uv run python voxgen.py --all -n 3                      # 3 variants each -> outpost_0..2 (plain run overwrites outpost.json)
uv run python voxgen.py -p "a windmill" --name windmill # free-form prompt
uv run python voxgen.py -p "an old gothic castle" --name castle --theme gothic

# themes: fantasy (default), gothic, middle_earth, cyberpunk, scifi_fantasy
uv run python voxgen.py --all -n 3 --theme scifi_fantasy -o outputs/scifi_fantasy

# output folder (default outputs/objects)
uv run python voxgen.py outpost -o outputs/test

# no LLM calls
uv run python voxgen.py --rerender                      # rebuild PNGs + sheet from existing .json
uv run python voxgen.py --sheet                         # rebuild _sheet.png only
uv run python voxgen.py outpost --pick 42 3 7           # deterministic spec pick + blit onto test terrain

# viewer (scans outputs/ recursively) -> http://localhost:8000
python viewer.py
```

Needs `OPENAI_API_KEY` in `.env`. Set `VOXGEN_PROVIDER=ollama` (+ `OLLAMA_MODEL`) to use a local model instead.

## How it works

Everything is in [voxgen.py](voxgen.py):

- **Spec** (what the LLM writes): `box`, `hollow_box` (closed shell), `column`, `pyramid`, `sphere`, `wedge`, `cylinder`, `erase` (doors/windows only) parts with a material.
- **Compiler**: applies parts in order, clamps to bounds, maps unknown materials to `stone`, keeps only the largest face-connected component (no floating voxels).
- **Templates**: `cave`, `skull`, `tree`, `boulder` are hand-made specs, not LLM output (`TEMPLATES`).
- **Output**: `{"brief_summary", "voxels": [{x,y,z,material}]}`.

The old TRELLIS text-to-mesh pipeline lives in [_misc/](_misc/).
