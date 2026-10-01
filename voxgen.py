"""Archetype -> LLM primitive spec -> voxel compiler -> VoxelObjectPlan JSON (+ PNG preview).

Usage:
    uv run python voxgen.py outpost            # one archetype
    uv run python voxgen.py --all              # every archetype
"""

import argparse
import json
import os
import sys
from pathlib import Path

MAX_SIZE = (12, 12, 20)
KIND_HINTS = {
    "building": "enclosed room(s) with a door and windows; someone lives or works here; show it (chimney, awning, ladder, balcony, sign pole)",
    "ruin facade": "exterior of a ruin: broken, uneven, half-gone; fallen arch, rubble, vegetation",
    "dungeon": "exterior entrance to a menacing way down: gate, bars, steps, skulls",
}
TWISTS = [
    "leaning dangerously", "on stilts", "half-collapsed", "overgrown", "with a rope bridge", "cantilevered overhangs",
    "sunken and flooded", "lopsided", "hanging banners", "crooked chimneys", "stacked like blocks",
    "hollow with a courtyard", "with a watchtower", "with a lean-to annex",
]
# Kinds the LLM can't draw well at this resolution; built from hand-made templates instead.
TEMPLATE_KINDS = ("cave", "natural")
MATERIALS = {"stone", "wood", "leaf", "grass", "water", "metal", "glass", "thatch", "cloth", "bone"}
COLORS = {
    "stone": "#7d7d7d", "wood": "#9c7f4e", "leaf": "#3b6e2a", "grass": "#5d8c3a", "water": "#3b5fa8",
    "metal": "#c6c6c6", "glass": "#a9cfe0", "thatch": "#b8a05a", "cloth": "#9e2b27", "bone": "#d9d3b8",
    # theme-renamed materials
    "concrete": "#6e6e6e", "corrugated_metal": "#8a8f93", "neon_signage": "#b04a9c", "solar_paneling": "#2b3a5c",
    "tinted_glass": "#4a6a7a", "slate": "#4b4f57", "dark_stone": "#454545", "timber": "#6b5233",
    "earth": "#866043", "sod": "#5d7a3a",
}

# Abstract slots the LLM can use instead of raw materials; each theme binds them.
SLOTS = ("primary_wall", "roof", "trim", "accent")
DEFAULT_SLOTS = {"primary_wall": "stone", "roof": "thatch", "trim": "wood", "accent": "cloth"}

# Theme = palette_override (base material -> themed name), slots, style text, massing modifiers,
# and surface trim applied after blitting.
THEMES = {
    "fantasy": {
        "style": "medium height, stone/wood/thatch, organic rooflines, ruined towers, fortified gates",
        "palette_override": {},
        "slots": {"primary_wall": "stone", "roof": "thatch", "trim": "wood", "accent": "cloth"},
        "massing_modifiers": {"verticality_scale": 1.0, "overhang_probability": 0.3},
        "surface_trim": {"material": "leaf", "chance": 0.08},
    },
    "gothic": {
        "style": "very tall, dark stone and slate with glass, steep gables, spiraling spires, cantilevered roof sections",
        "palette_override": {"stone": "dark_stone", "thatch": "slate"},
        "slots": {"primary_wall": "stone", "roof": "thatch", "trim": "metal", "accent": "glass"},
        "massing_modifiers": {"verticality_scale": 1.8, "overhang_probability": 0.6},
        "surface_trim": {"material": "leaf", "chance": 0.04},
    },
    "middle_earth": {
        "style": "organic, heavy masonry, timber bracing, earthen roofs, entrances built into a cliff or hill",
        "palette_override": {"wood": "timber", "thatch": "sod"},
        "slots": {"primary_wall": "stone", "roof": "grass", "trim": "wood", "accent": "leaf"},
        "massing_modifiers": {"verticality_scale": 0.7, "overhang_probability": 0.2},
        "surface_trim": {"material": "grass", "chance": 0.15},
    },
    "cyberpunk": {
        "style": "extreme height, grid-aligned slabs, dense overhangs, HVAC conduits, cantilevered billboards",
        "palette_override": {"stone": "concrete", "wood": "corrugated_metal", "leaf": "neon_signage",
                             "thatch": "solar_paneling", "glass": "tinted_glass"},
        "slots": {"primary_wall": "stone", "roof": "thatch", "trim": "metal", "accent": "leaf"},
        "massing_modifiers": {"verticality_scale": 2.5, "overhang_probability": 0.8},
        "surface_trim": {"material": "leaf", "chance": 0.05},
    },
}
DEFAULT_THEME = "fantasy"

ARCHETYPE_ROOM_PROFILE = {
    "settlement": {"tags": ["bustling", "residential", "civic", "well-lit", "market-adjacent"], "kinds": ["building"]},
    "outpost": {"tags": ["spartan", "guarded", "windswept", "cramped"], "kinds": ["building"]},
    "stronghold": {"tags": ["fortified", "guarded", "defensible", "cold stone"], "kinds": ["building"]},
    "sanctuary": {"tags": ["hushed", "sacred", "candlelit", "serene"], "kinds": ["building", "cave"]},
    "residence": {"tags": ["homely", "quiet", "lived-in", "cluttered"], "kinds": ["building"]},
    "worksite": {"tags": ["industrious", "noisy", "cluttered", "smoky"], "kinds": ["building"]},
    "market": {"tags": ["busy", "cluttered", "loud", "colorful"], "kinds": ["building"]},
    "ruin": {"tags": ["overgrown", "collapsed", "weathered", "looted"], "kinds": ["ruin facade"]},
    "dungeon": {"tags": ["dark", "trapped", "collapsed", "flooded", "haunted"], "kinds": ["dungeon"]},
    "landmark": {"tags": ["awe-inspiring", "exposed", "windswept", "eroded"], "kinds": ["ruin facade", "natural"]},
    "resource_site": {"tags": ["industrious", "cramped", "dusty", "echoing"], "kinds": ["cave", "building"]},
    "natural_feature": {"tags": ["wild", "untouched", "echoing", "damp"], "kinds": ["natural", "cave"]},
    "passage": {"tags": ["narrow", "echoing", "drafty", "worn"], "kinds": ["cave", "ruin facade"]},
}

SYSTEM_PROMPT = f"""You design tiny voxel structures for a map. Output ONLY a JSON object, no prose.
Every building must be a real enclosed structure that could hold a room: a closed hollow_box shell
(walls, floor and ceiling) with at least one door at ground level (erase 1 wide x 2 tall through a wall)
and a few 1-voxel window erases. Never erase the interior volume or the floor/ceiling; hollow_box is already hollow.
Put the roof on top of the shell. Add silhouette and facade detail around that enclosed core.

Schema:
{{
  "summary": "<exterior description>",
  "parts": [ <part>, ... ]
}}
Each part is one of:
  {{"shape": "box",        "from": [x,y,z], "to": [x,y,z], "material": M}}   # filled box, inclusive corners
  {{"shape": "hollow_box", "from": [x,y,z], "to": [x,y,z], "material": M}}   # walls only, open interior
  {{"shape": "column",     "at": [x,y], "z": [z0,z1], "material": M}}         # 1x1 vertical column
  {{"shape": "pyramid",    "from": [x,y,z], "to": [x,y], "material": M}}      # base rect at z, steps inward each layer up
  {{"shape": "sphere",     "at": [x,y,z], "r": R, "material": M}}            # ball (canopies, boulders, domes)
  {{"shape": "wedge",      "from": [x,y,z], "to": [x,y,z], "material": M}}   # ramp rising along x from 'from' to 'to'
  {{"shape": "cylinder",   "at": [x,y,z], "r": R, "h": H, "material": M}}    # vertical round tower/pipe, base at z
  {{"shape": "erase",      "from": [x,y,z], "to": [x,y,z]}}                   # remove voxels (exterior doors, windows, gaps)

Grid: x,y in 0..{MAX_SIZE[0]-1}, z in 0..{MAX_SIZE[2]-1} (z=0 is ground). Parts apply in order.
M is exactly one of: {", ".join(sorted(MATERIALS))}, or an abstract slot: {", ".join(SLOTS)}.
Prefer slots (primary_wall for walls, roof for roofs, trim for edges and frames, accent for signs/banners/details);
the theme maps them to real materials.
Everything must touch the ground or another part (no floating pieces). Use 6-14 parts.
Be weird and specific. Asymmetry, overhangs, odd silhouettes, mixed materials, a detail that tells a story.
Never a plain box with a roof. Never centered and symmetric. No part bigger than 8x8x8; build from several
smaller pieces at different heights and offsets. Use erase to punch windows, doors, gaps."""


# ---------------------------------------------------------------- compile

def _rect(a, b):
    xs, ys = sorted([a[0], b[0]]), sorted([a[1], b[1]])
    return range(xs[0], xs[1] + 1), range(ys[0], ys[1] + 1)


def _cells(part):
    s = part["shape"]
    if s in ("box", "hollow_box", "erase"):
        f, t = part["from"], part["to"]
        xs, ys = _rect(f, t)
        zs = range(min(f[2], t[2]), max(f[2], t[2]) + 1)
        for x in xs:
            for y in ys:
                for z in zs:
                    edge = (x in (xs[0], xs[-1]) or y in (ys[0], ys[-1])
                            or z in (zs[0], zs[-1]))  # closed shell: walls + floor + ceiling
                    if s != "hollow_box" or edge:
                        yield x, y, z
    elif s == "column":
        x, y = part["at"]
        for z in range(min(part["z"]), max(part["z"]) + 1):
            yield x, y, z
    elif s == "sphere":
        cx, cy, cz = part["at"]
        r = part["r"]
        for x in range(cx - r, cx + r + 1):
            for y in range(cy - r, cy + r + 1):
                for z in range(cz - r, cz + r + 1):
                    if (x-cx)**2 + (y-cy)**2 + (z-cz)**2 <= r*r + r*0.5:
                        yield x, y, z
    elif s == "cylinder":
        cx, cy, cz = part["at"]
        r, h = part["r"], part.get("h", 1)
        for x in range(cx - r, cx + r + 1):
            for y in range(cy - r, cy + r + 1):
                if (x-cx)**2 + (y-cy)**2 <= r*r + r*0.5:
                    for z in range(cz, cz + h):
                        yield x, y, z
    elif s == "wedge":
        f, t = part["from"], part["to"]
        xs, ys = _rect(f, t)
        z0, z1 = f[2], t[2]
        for i, x in enumerate(xs):
            top = round(z0 + (z1 - z0) * i / max(len(xs) - 1, 1))
            for y in ys:
                for z in range(min(z0, top), top + 1):
                    yield x, y, z
    elif s == "pyramid":
        x0, y0, z0 = part["from"]
        x1, y1 = part["to"][:2]
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        z = z0
        while x0 <= x1 and y0 <= y1:
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    yield x, y, z
            x0, x1, y0, y1, z = x0 + 1, x1 - 1, y0 + 1, y1 - 1, z + 1


def get_theme(name):
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def resolve_material(mat, theme):
    """Slot or material name -> base material (themed renaming happens later, in to_plan)."""
    mat = {**DEFAULT_SLOTS, **theme["slots"]}.get(mat, mat)
    return mat if mat in MATERIALS else "stone"


def compile_spec(spec, size=MAX_SIZE, theme=None):
    """Parts -> {(x,y,z): material}. Clamps bounds, binds slots, fixes materials, drops floating pieces."""
    theme = theme or get_theme(spec.get("theme"))
    grid = {}
    for part in spec.get("parts", []):
        mat = resolve_material(part.get("material", "primary_wall"), theme)
        try:
            cells = list(_cells(part))
        except (KeyError, TypeError, ValueError):
            continue  # malformed part: skip it
        if part["shape"] == "erase" and len(cells) > 1:
            f, t = part["from"], part["to"]
            if all(abs(f[i] - t[i]) >= 1 for i in range(3)):
                continue  # blob-sized erase would gut the walls/floor/roof; only doors/windows allowed
        for c in cells:
            if all(0 <= c[i] < size[i] for i in range(3)):
                if part["shape"] == "erase":
                    grid.pop(c, None)
                else:
                    grid[c] = mat
    _support_floating(grid)
    return _largest_component(grid)


def _components(grid):
    seen = set()
    for start in grid:
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            comp.add(c)
            x, y, z = c
            for n in ((x+1,y,z),(x-1,y,z),(x,y+1,z),(x,y-1,z),(x,y,z+1),(x,y,z-1)):
                if n in grid and n not in seen:
                    stack.append(n)
        yield comp


def _support_floating(grid):
    """Drop a column from each floating piece down to whatever is below it (or the ground)."""
    for comp in list(_components(grid)):
        if any(z == 0 for _, _, z in comp):
            continue
        x, y, z = min(comp, key=lambda c: c[2])
        mat = grid[(x, y, z)]
        while z > 0 and (x, y, z - 1) not in grid:
            z -= 1
            grid[(x, y, z)] = mat


def _largest_component(grid):
    best = max(_components(grid), key=len, default=set())
    return {c: grid[c] for c in best}


def to_plan(summary, grid, theme=None):
    pal = (theme or {}).get("palette_override", {})
    voxels = [{"x": x, "y": y, "z": z, "material": pal.get(m, m)} for (x, y, z), m in sorted(grid.items())]
    return {"brief_summary": summary, "voxels": voxels}


# ---------------------------------------------------------------- templates (hand-made organic shapes)

def _tmpl_cave(rng):
    cx, cy = rng.randint(5, 6), rng.randint(5, 6)
    depth = rng.randint(4, 6)
    parts = [{"shape": "sphere", "at": [cx, cy, 0], "r": 5, "material": "stone"},
             {"shape": "sphere", "at": [cx + rng.choice([-3, 3]), cy, 0], "r": 3, "material": "stone"},
             {"shape": "sphere", "at": [cx, cy, 4], "r": 2, "material": "grass"}]
    for y in range(depth):  # tunnel mouth, one slice at a time (blob erases are rejected)
        parts.append({"shape": "erase", "from": [cx - 1, y, 0], "to": [cx, y, 2]})
    return {"summary": "A rocky hillside with a dark tunnel mouth and a mossy crown.", "parts": parts}


def _tmpl_skull(rng):
    parts = [{"shape": "sphere", "at": [6, 6, 6], "r": 4, "material": "bone"},
             {"shape": "box", "from": [4, 4, 0], "to": [8, 7, 2], "material": "bone"},
             {"shape": "erase", "from": [4, 3, 5], "to": [5, 3, 6]}, {"shape": "erase", "from": [7, 3, 5], "to": [8, 3, 6]},
             {"shape": "erase", "from": [4, 4, 5], "to": [5, 4, 6]}, {"shape": "erase", "from": [7, 4, 5], "to": [8, 4, 6]},
             {"shape": "erase", "from": [6, 3, 4], "to": [6, 3, 4]},
             {"shape": "erase", "from": [5, 4, 0], "to": [5, 4, 1]}, {"shape": "erase", "from": [7, 4, 0], "to": [7, 4, 1]},
             {"shape": "sphere", "at": [rng.choice([3, 9]), 9, 1], "r": 1, "material": "grass"}]
    return {"summary": "A giant half-buried skull staring out of the ground, eye sockets hollow.", "parts": parts}


def _tmpl_tree(rng):
    h = rng.randint(7, 10)
    parts = [{"shape": "column", "at": [6, 6], "z": [0, h], "material": "wood"},
             {"shape": "column", "at": [5, 6], "z": [0, 1], "material": "wood"},
             {"shape": "column", "at": [7, 6], "z": [0, 1], "material": "wood"},
             {"shape": "sphere", "at": [6, 6, h + 1], "r": 3, "material": "leaf"},
             {"shape": "sphere", "at": [rng.choice([3, 9]), 6, h - 1], "r": 2, "material": "leaf"},
             {"shape": "column", "at": [5, 6], "z": [h - 3, h - 2], "material": "wood"}]
    return {"summary": "A lone gnarled tree with a wide leafy canopy.", "parts": parts}


def _tmpl_boulder(rng):
    parts = [{"shape": "sphere", "at": [6, 6, 1], "r": 4, "material": "stone"},
             {"shape": "sphere", "at": [rng.choice([3, 9]), rng.choice([4, 8]), 1], "r": 2, "material": "stone"},
             {"shape": "sphere", "at": [6, 6, 5], "r": 2, "material": "grass"}]
    return {"summary": "A mossy boulder with a smaller rock beside it.", "parts": parts}


TEMPLATES = {"cave": [_tmpl_cave], "natural": [_tmpl_skull, _tmpl_tree, _tmpl_boulder]}


# ---------------------------------------------------------------- blit into world

def blit(world, grid, origin, ground, theme=None, rng=None):
    """Stamp a compiled shell into world {(x,y,z): material} at origin=(ox,oy,oz).

    ground(x, y) -> terrain top z at that world column. Steps: clear air box, blit shell,
    cast foundation columns down to terrain, apply thematic surface trim.
    """
    import random
    theme = theme or get_theme(None)
    rng = rng or random.Random(0)
    ox, oy, oz = origin
    pal = theme.get("palette_override", {})
    # 1. clear the bounding envelope of the structure
    for x, y, z in {(ox + x, oy + y, oz + z) for x in range(MAX_SIZE[0]) for y in range(MAX_SIZE[1])
                    for z in range(MAX_SIZE[2])}:
        world.pop((x, y, z), None)
    # 2. blit the shell
    placed = {(ox + x, oy + y, oz + z): pal.get(m, m) for (x, y, z), m in grid.items()}
    world.update(placed)
    # 3. foundation: lowest voxel of each column that hovers above terrain casts a pillar down
    lowest = {}
    for (x, y, z) in placed:
        if (x, y) not in lowest or z < lowest[(x, y)]:
            lowest[(x, y)] = z
    for (x, y), z in lowest.items():
        for fz in range(max(ground(x, y), 0), z):
            world.setdefault((x, y, fz), placed[(x, y, z)])
    # 4. thematic trim on exposed tops
    trim = theme.get("surface_trim")
    if trim:
        for (x, y, z) in sorted(placed):
            if (x, y, z + 1) not in world and rng.random() < trim["chance"]:
                world[(x, y, z + 1)] = pal.get(trim["material"], trim["material"])
    return world


def pick_spec(pool_dir, world_seed, chunk, structure_id):
    """Deterministic: Hash(world_seed, chunk_coords, structure_id) -> one spec in the offline pool."""
    import hashlib
    pool = sorted(Path(pool_dir).glob("*.spec.json"))
    pool = [p for p in pool if p.name.startswith(structure_id)] or pool
    if not pool:
        raise FileNotFoundError(f"no specs in {pool_dir}")
    h = hashlib.sha256(f"{world_seed}:{chunk[0]},{chunk[1]}:{structure_id}".encode()).digest()
    return pool[int.from_bytes(h[:8], "big") % len(pool)]


# ---------------------------------------------------------------- render

def render_png(grid, path, title=""):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    filled = np.zeros(MAX_SIZE, dtype=bool)
    colors = np.empty(MAX_SIZE, dtype=object)
    for (x, y, z), m in grid.items():
        filled[x, y, z] = True
        colors[x, y, z] = COLORS.get(m, "#999999")
    fig = plt.figure(figsize=(6, 8))
    ax = fig.add_subplot(projection="3d")
    ax.voxels(filled, facecolors=colors, edgecolor="k", linewidth=0.15)
    ax.set_proj_type("ortho")
    ax.set_xlim(0, MAX_SIZE[0]); ax.set_ylim(0, MAX_SIZE[1]); ax.set_zlim(0, MAX_SIZE[2])
    ax.set_box_aspect(MAX_SIZE)
    ax.set_axis_off()
    ax.set_title(title, fontsize=9)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def contact_sheet(out_dir, cols=7):
    """Tile every render in out_dir into _sheet.png, trimming whitespace."""
    from PIL import Image, ImageChops
    ims = []
    for p in sorted(Path(out_dir).glob("*.png")):
        if p.name.startswith("_"):
            continue
        im = Image.open(p).convert("RGB")
        bbox = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
        ims.append(im.crop(bbox) if bbox else im)
    if not ims:
        return
    w, h = max(i.width for i in ims), max(i.height for i in ims)
    rows = -(-len(ims) // cols)
    sheet = Image.new("RGB", (w * cols, h * rows), "white")
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w + (w - im.width) // 2, (i // cols) * h + h - im.height))
    sheet.save(Path(out_dir) / "_sheet.png")


# ---------------------------------------------------------------- llm

def _load_env():
    env = Path(__file__).with_name(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def generate(system, user):
    """Provider-agnostic text completion. VOXGEN_PROVIDER=openai (default) | ollama."""
    _load_env()
    provider = os.environ.get("VOXGEN_PROVIDER", "openai")
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        r = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=float(os.environ.get("VOXGEN_TEMP", "1.1")),
        )
        return r.choices[0].message.content
    if provider == "ollama":
        import urllib.request
        body = json.dumps({"model": os.environ.get("OLLAMA_MODEL", "llama3.1"), "stream": False, "format": "json",
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", body, {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())["message"]["content"]
    raise ValueError(f"unknown provider {provider}")


def theme_prompt(theme_name):
    t = get_theme(theme_name)
    m = t["massing_modifiers"]
    return (f"Theme: {theme_name}. Style: {t['style']}. Verticality x{m['verticality_scale']} "
            f"(taller than usual if >1, squatter if <1). Overhangs/cantilevers: {int(m['overhang_probability'] * 100)}% likely.")


def generate_archetype(name, out_dir, rng=None, theme=DEFAULT_THEME):
    import random
    rng = rng or random.Random()
    p = ARCHETYPE_ROOM_PROFILE[name.rsplit("_", 1)[0] if name not in ARCHETYPE_ROOM_PROFILE else name]
    kind = rng.choice(p["kinds"])
    if kind in TEMPLATE_KINDS:
        return finish_spec(rng.choice(TEMPLATES[kind])(rng), f"{name} ({kind})", name, out_dir, theme)
    twist = ", ".join(rng.sample(TWISTS, 2))
    user = (f"Archetype: {name}. Kind: {kind} ({KIND_HINTS[kind]}). Mood: {', '.join(p['tags'])}. "
            f"Twist: {twist}. {theme_prompt(theme)} Use the slots primary_wall, roof, trim, accent.")
    generate_object(user, name, out_dir, theme)


def generate_object(user, name, out_dir, theme=DEFAULT_THEME):
    """Free-form description -> spec -> compiled plan + PNG."""
    finish_spec(json.loads(generate(SYSTEM_PROMPT, user)), user, name, out_dir, theme)


def finish_spec(spec, user, name, out_dir, theme):
    """Spec -> compiled plan + PNG on disk."""
    spec["prompt"] = user
    spec["theme"] = theme
    t = get_theme(theme)
    grid = compile_spec(spec, theme=t)
    spec["compiled_voxels"] = len(grid)
    plan = to_plan(spec.get("summary", name), grid, t)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.spec.json").write_text(json.dumps(spec, indent=2))
    (out_dir / f"{name}.json").write_text(json.dumps(plan, indent=2))
    render_png({c: t["palette_override"].get(m, m) for c, m in grid.items()}, out_dir / f"{name}.png", name)
    print(f"{name}: {len(grid)} voxels -> {out_dir / name}.json/.png")


def demo_pick(pool, world_seed, cx, cy, structure_id, theme_name):
    """Pick a pooled spec by hash, blit it onto bumpy terrain, print what happened."""
    path = pick_spec(pool, world_seed, (cx, cy), structure_id)
    spec = json.loads(path.read_text())
    t = get_theme(theme_name or spec.get("theme"))
    grid = compile_spec(spec, theme=t)
    ground = lambda x, y: (x * 3 + y * 5) % 3  # stand-in terrain noise
    world = blit({}, grid, (0, 0, 2), ground, t)
    print(f"{path.name}: {len(grid)} shell voxels -> {len(world)} world voxels (incl. foundation + trim)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archetype", nargs="?", choices=sorted(ARCHETYPE_ROOM_PROFILE))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-p", "--prompt", help="free-form description instead of an archetype")
    ap.add_argument("--name", help="output name for --prompt (default: slug of the prompt)")
    ap.add_argument("-o", "--out", default="outputs/objects")
    ap.add_argument("-n", type=int, default=1, help="variants per archetype")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--theme", default=DEFAULT_THEME, choices=sorted(THEMES))
    ap.add_argument("--pick", nargs=3, type=int, metavar=("WORLD_SEED", "CX", "CY"),
                    help="deterministically pick a pooled spec for ARCHETYPE and blit it onto test terrain")
    ap.add_argument("--sheet", action="store_true", help="only rebuild _sheet.png from existing renders")
    ap.add_argument("--rerender", action="store_true", help="rebuild PNGs + sheet from existing .json (no LLM)")
    args = ap.parse_args()
    if args.pick:
        sys.exit(demo_pick(args.out, args.pick[0], args.pick[1], args.pick[2], args.archetype or "", args.theme))
    if args.rerender:
        for p in Path(args.out).glob("*.json"):
            if not p.name.endswith(".spec.json"):
                plan = json.loads(p.read_text())
                grid = {(v["x"], v["y"], v["z"]): v["material"] for v in plan["voxels"]}
                render_png(grid, p.with_suffix(".png"), p.stem)
    if args.prompt:
        import re
        name = args.name or re.sub(r"[^a-z0-9]+", "_", args.prompt.lower()).strip("_")[:40]
        for i in range(args.n):
            generate_object(args.prompt, name if args.n == 1 else f"{name}_{i}", Path(args.out), args.theme)
        contact_sheet(args.out)
        sys.exit()
    if args.sheet or args.rerender:
        sys.exit(contact_sheet(args.out))
    names = sorted(ARCHETYPE_ROOM_PROFILE) if args.all else [args.archetype]
    if names == [None]:
        sys.exit(ap.print_help())
    import random
    rng = random.Random(args.seed)
    for n in names:
        for i in range(args.n):
            generate_archetype(n if args.n == 1 else f"{n}_{i}", Path(args.out), rng, args.theme)
    contact_sheet(args.out)
