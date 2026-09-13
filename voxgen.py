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

MAX_SIZE = (16, 16, 32)
MATERIALS = {"stone", "wood", "leaf", "grass", "water", "metal", "glass", "thatch", "cloth", "bone"}
COLORS = {
    "stone": "#8a8a8a", "wood": "#8b5a2b", "leaf": "#3f8f3f", "grass": "#6abf4b", "water": "#3f7fbf",
    "metal": "#b0b8c0", "glass": "#bfe6ff", "thatch": "#c9a54a", "cloth": "#b03060", "bone": "#e8e2c8",
}

ARCHETYPE_ROOM_PROFILE = {
    "settlement": {"tags": ["bustling", "residential", "civic", "well-lit", "market-adjacent"], "kinds": ["building"]},
    "outpost": {"tags": ["spartan", "guarded", "windswept", "cramped"], "kinds": ["building"]},
    "stronghold": {"tags": ["fortified", "guarded", "defensible", "cold stone"], "kinds": ["building"]},
    "sanctuary": {"tags": ["hushed", "sacred", "candlelit", "serene"], "kinds": ["building", "cave"]},
    "residence": {"tags": ["homely", "quiet", "lived-in", "cluttered"], "kinds": ["building"]},
    "worksite": {"tags": ["industrious", "noisy", "cluttered", "smoky"], "kinds": ["building"]},
    "market": {"tags": ["busy", "cluttered", "loud", "colorful"], "kinds": ["building"]},
    "ruin": {"tags": ["overgrown", "collapsed", "weathered", "looted"], "kinds": ["ruin interior"]},
    "dungeon": {"tags": ["dark", "trapped", "collapsed", "flooded", "haunted"], "kinds": ["dungeon"]},
    "landmark": {"tags": ["awe-inspiring", "exposed", "windswept", "eroded"], "kinds": ["ruin interior", "cave"]},
    "resource_site": {"tags": ["industrious", "cramped", "dusty", "echoing"], "kinds": ["cave", "building"]},
    "natural_feature": {"tags": ["wild", "untouched", "echoing", "damp"], "kinds": ["cave"]},
    "passage": {"tags": ["narrow", "echoing", "drafty", "worn"], "kinds": ["cave", "ruin interior"]},
}

SYSTEM_PROMPT = f"""You design tiny voxel objects for a fantasy map. Output ONLY a JSON object, no prose.

Schema:
{{
  "summary": "<one sentence>",
  "parts": [ <part>, ... ]
}}
Each part is one of:
  {{"shape": "box",        "from": [x,y,z], "to": [x,y,z], "material": M}}   # filled box, inclusive corners
  {{"shape": "hollow_box", "from": [x,y,z], "to": [x,y,z], "material": M}}   # walls only, open interior
  {{"shape": "column",     "at": [x,y], "z": [z0,z1], "material": M}}         # 1x1 vertical column
  {{"shape": "pyramid",    "from": [x,y,z], "to": [x,y], "material": M}}      # base rect at z, steps inward each layer up
  {{"shape": "erase",      "from": [x,y,z], "to": [x,y,z]}}                   # remove voxels (doors, windows)

Grid: x,y in 0..{MAX_SIZE[0]-1}, z in 0..{MAX_SIZE[2]-1} (z=0 is ground). Parts apply in order.
M is exactly one of: {", ".join(sorted(MATERIALS))}.
Everything must touch the ground or another part (no floating pieces). Use 5-15 parts.
Use most of the footprint: buildings should be wide and grounded, not thin towers, unless the archetype calls for one."""


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
                    edge = x in (xs[0], xs[-1]) or y in (ys[0], ys[-1])
                    if s != "hollow_box" or edge:
                        yield x, y, z
    elif s == "column":
        x, y = part["at"]
        for z in range(min(part["z"]), max(part["z"]) + 1):
            yield x, y, z
    elif s == "pyramid":
        x0, y0, z0 = part["from"]
        x1, y1 = part["to"]
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        z = z0
        while x0 <= x1 and y0 <= y1:
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    yield x, y, z
            x0, x1, y0, y1, z = x0 + 1, x1 - 1, y0 + 1, y1 - 1, z + 1


def compile_spec(spec, size=MAX_SIZE):
    """Parts -> {(x,y,z): material}. Clamps bounds, fixes materials, drops floating pieces."""
    grid = {}
    for part in spec.get("parts", []):
        mat = part.get("material", "stone")
        mat = mat if mat in MATERIALS else "stone"
        for c in _cells(part):
            if all(0 <= c[i] < size[i] for i in range(3)):
                if part["shape"] == "erase":
                    grid.pop(c, None)
                else:
                    grid[c] = mat
    return _largest_component(grid)


def _largest_component(grid):
    seen, best = set(), {}
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
        if len(comp) > len(best):
            best = comp
    return {c: grid[c] for c in best}


def to_plan(summary, grid):
    voxels = [{"x": x, "y": y, "z": z, "material": m} for (x, y, z), m in sorted(grid.items())]
    return {"brief_summary": summary, "voxels": voxels}


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
        colors[x, y, z] = COLORS[m]
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
        )
        return r.choices[0].message.content
    if provider == "ollama":
        import urllib.request
        body = json.dumps({"model": os.environ.get("OLLAMA_MODEL", "llama3.1"), "stream": False, "format": "json",
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", body, {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())["message"]["content"]
    raise ValueError(f"unknown provider {provider}")


def generate_archetype(name, out_dir):
    p = ARCHETYPE_ROOM_PROFILE[name]
    user = f"Archetype: {name}. Kind: {' or '.join(p['kinds'])}. Mood: {', '.join(p['tags'])}."
    spec = json.loads(generate(SYSTEM_PROMPT, user))
    grid = compile_spec(spec)
    plan = to_plan(spec.get("summary", name), grid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.spec.json").write_text(json.dumps(spec, indent=2))
    (out_dir / f"{name}.json").write_text(json.dumps(plan, indent=2))
    render_png(grid, out_dir / f"{name}.png", f"{name}: {plan['brief_summary']}")
    print(f"{name}: {len(grid)} voxels -> {out_dir / name}.json/.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archetype", nargs="?", choices=sorted(ARCHETYPE_ROOM_PROFILE))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-o", "--out", default="outputs/objects")
    args = ap.parse_args()
    names = sorted(ARCHETYPE_ROOM_PROFILE) if args.all else [args.archetype]
    if names == [None]:
        sys.exit(ap.print_help())
    for n in names:
        generate_archetype(n, Path(args.out))
