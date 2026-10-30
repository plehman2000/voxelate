"""Generate voxel models from an archetype, description, or point-of-interest block.

Game entry point: voxgen.generate(poi, region=..., world=..., palette=[...]).

Examples:
    uv run python voxgen.py outpost
    uv run python voxgen.py --poi inputs\\hourn_glut.md --out outputs\\poi
    uv run python voxgen.py --description "a windmill" --name windmill
"""

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_SIZE = (12, 12, 20)
KIND_HINTS = {
    "building": "enclosed room(s) with a door and windows; someone lives or works here; show it (chimney, awning, ladder, balcony, sign pole)",
    "ruin facade": "exterior of a ruin: broken, uneven, half-gone; fallen arch, rubble, vegetation",
    "dungeon": "exterior entrance to a menacing way down: gate, bars, steps, skulls",
    "cave": "a cave or cavern entrance integrated into the surrounding rock",
    "natural": "a distinctive natural landmark or terrain feature",
}
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
    "scifi_fantasy": {
        "style": "fantasy stonework fused with sci-fi tech: stone towers crowned with glowing neon crystals, "
                 "metal pipes and antennae, glass domes, slate roofs, ancient ruins with embedded machinery",
        "palette_override": {"thatch": "slate", "leaf": "neon_signage", "glass": "tinted_glass"},
        "slots": {"primary_wall": "stone", "roof": "thatch", "trim": "metal", "accent": "leaf"},
        "massing_modifiers": {"verticality_scale": 1.4, "overhang_probability": 0.5},
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

ARCHETYPE_RECIPES = {
    "settlement": "Build a compact cluster of at least three distinct small structures around one shared central feature; vary heights and rooflines so it reads as a tiny village, not one large building.",
    "outpost": "Build one tall, narrow watchtower with a footprint no wider than 5 voxels and a tower height of at least 8 voxels. Show exposed support posts at the base, a projecting one-sided lookout near the top, a small roof cap, and a distinct ground-level entrance. Keep the silhouette vertical; do not add a broad wing or bridge.",
    "stronghold": "Build a compact fortified keep with a tall central mass, a visibly thick perimeter wall, one unmistakable gate opening, and two unequal corner towers or raised bastions. Keep the gate readable from the outside.",
    "sanctuary": "Build a quiet, enclosed central chamber with a clearly elevated roof or spire, a deliberate entrance, and a small forecourt or altar feature aligned with that entrance. Use balanced proportions without making the whole structure symmetrical.",
    "residence": "Build a small, clearly habitable house: one enclosed main room, a visible ground-level door, a pitched or stepped roof, and a chimney or porch that makes it feel lived-in. Keep the silhouette lower and wider than a watchtower.",
    "worksite": "Build an enclosed workshop as the main mass, with a tall chimney or exhaust stack, one attached work-yard feature such as a crane or loading awning, and a visible entrance. Make the industrial equipment visibly distinct from the walls.",
    "market": "Build two or three small stall-sized masses around a shared open aisle. Give each stall a visibly projecting awning and vary their heights or colors; do not merge them into one solid hall.",
    "ruin": "Build an incomplete, broken facade rather than a usable intact building: leave a major gap in the wall or roof, vary the surviving wall heights, and include a fallen arch or rubble at the base.",
    "dungeon": "Build a clear entrance descending underground: frame a dark doorway or stair opening with a heavy lintel or gate, flanking stone masses, and steps or a sloped approach leading into it. The entrance must be the dominant feature.",
    "landmark": "Build one immediately recognizable, unusual silhouette with a dominant vertical or arched feature and a distinct base. Use unequal projections and openings; avoid a generic rectangular building.",
    "resource_site": "Build a mine or extraction entrance as the focal point, framed by supports or a heavy lintel, with a small attached work platform and one visible extraction feature such as a winch, ore chute, or cart.",
    "natural_feature": "Build one coherent natural silhouette such as a jagged rock spire, giant tree, or cave mouth; vary the outline and keep the base visibly grounded. Do not add building walls unless the chosen kind calls for a cave entrance.",
    "passage": "Build a narrow, traversable route marked by a prominent arch, portal, or cave mouth. Keep the opening visibly clear and frame it with uneven sides, broken masonry, or rock formations.",
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
M is exactly one of: {", ".join(sorted(MATERIALS))}, an abstract slot: {", ".join(SLOTS)},
or a literal #RRGGBB color when the request explicitly provides one.
Prefer slots (primary_wall for walls, roof for roofs, trim for edges and frames, accent for signs/banners/details);
the theme maps them to real materials.
When an explicit hex color is requested, use that exact color on the part it describes; do not approximate it.
Everything must touch the ground or another part (no floating pieces). Use 6-14 parts.
Never build terrain: no ground plate, plinth, paved lot, patio, or base slab under or around the
structure, and nothing that reads as the surrounding land. The game drops the model onto its own
terrain, so any ground you build floats above the real one. z=0 is where your walls and floor start.
Use ONLY the shapes listed above; anything else is dropped. Cylinder/sphere "at" must have 3 numbers.
Example (castle keep, 12 wide): hollow_box [1,1,0]-[10,10,9]; four corner cylinders at [1,1,0],[10,1,0],[1,10,0],[10,10,0]
r=1 h=13; battlements = box [1,1,9]-[10,10,9] then erase every other top-edge voxel one at a time; gate = erase [5,1,0]-[6,1,2];
slit windows = erase [3,1,5]-[3,1,6]; roof = pyramid on a small tower, not on the whole keep.
Be weird and specific. Asymmetry, overhangs, odd silhouettes, mixed materials, a detail that tells a story.
Every feature named in summary or requested by the prompt must be visibly built by the parts; never claim
spires, towers, bridges, or openings that are absent from the voxel geometry.
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
    if _is_hex_color(mat):
        return mat.lower()
    mat = {**DEFAULT_SLOTS, **theme["slots"]}.get(mat, mat)
    return mat if mat in MATERIALS else "stone"


def _is_hex_color(value):
    return (isinstance(value, str) and len(value) == 7 and value[0] == "#"
            and all(char in "0123456789abcdefABCDEF" for char in value[1:]))


def _explicit_slot_colors(user):
    slots = {
        "wall": "primary_wall", "walls": "primary_wall", "facade": "primary_wall", "body": "primary_wall",
        "roof": "roof", "trim": "trim",
        "accent": "accent", "glass": "accent", "window": "accent", "windows": "accent",
    }
    colors = {}
    for match in re.finditer(r"#[0-9a-fA-F]{6}\b", user):
        line_start = user.rfind("\n", 0, match.start()) + 1
        if user[line_start:match.start()].strip().lower().startswith(
            ("explicit hex colors:", "allowed colors:")
        ):
            continue
        context = re.split(r"[\n.!?;]", user[max(0, match.start() - 64):match.start()])[-1]
        mentions = list(re.finditer(r"\b(?:walls?|facade|body|roof|trim|accent|glass|windows?)\b", context, re.I))
        slot = slots[mentions[-1].group().lower()] if mentions else "accent"
        colors[slot] = match.group().lower()
    return colors


def _apply_explicit_slot_colors(spec, user, theme):
    for slot, color in _explicit_slot_colors(user).items():
        parts = spec.get("parts", [])
        matching_parts = [part for part in parts if part.get("material") == slot]
        if not matching_parts:
            material = theme["slots"].get(slot, DEFAULT_SLOTS[slot])
            matching_parts = [part for part in parts if part.get("material") == material]
        for part in matching_parts:
            part["material"] = color


def _apply_allowed_colors(spec, allowed_colors):
    if not allowed_colors:
        return
    palette = tuple(dict.fromkeys(color.lower() for color in allowed_colors))
    assigned = {}
    for part in spec.get("parts", []):
        if part.get("shape") == "erase":
            continue
        material = part.get("material", "primary_wall")
        if material in palette:
            continue
        if material not in assigned:
            assigned[material] = palette[len(assigned) % len(palette)]
        part["material"] = assigned[material]


def _add_gothic_spires(spec, theme, allowed_colors=None):
    """Add three grounded, capped spires above open roof positions in gothic models."""
    if theme != "gothic":
        return
    parts = spec.setdefault("parts", [])
    generated = [part for part in parts if part.get("_generated_feature") == "gothic_spire"]
    if generated:
        spec["parts"] = [part for part in parts if part not in generated]
    elif spec.get("gothic_spires_added"):
        old_count = spec["gothic_spires_added"]
        old_parts = parts[-old_count * 2:]
        if all(
            old_parts[index].get("shape") == "column"
            and old_parts[index + 1].get("shape") == "pyramid"
            for index in range(0, len(old_parts), 2)
        ):
            spec["parts"] = parts[:-old_count * 2]
    spec.pop("gothic_spires_added", None)

    grid = compile_spec(spec, theme=get_theme(theme))
    tops = {}
    for x, y, z in grid:
        tops[(x, y)] = max(z, tops.get((x, y), -1))
    if not tops:
        return

    max_z = max(tops.values())
    material = (allowed_colors[0].lower() if allowed_colors else
                get_theme(theme)["palette_override"].get(
                    get_theme(theme)["slots"]["primary_wall"],
                    get_theme(theme)["slots"]["primary_wall"],
                ))
    candidates = []
    for (x, y), base_z in tops.items():
        if x < 1 or x >= MAX_SIZE[0] - 1 or y < 1 or y >= MAX_SIZE[1] - 1:
            continue
        if base_z < max_z - 5:
            continue
        spire_height = min(5, MAX_SIZE[2] - base_z - 1)
        if spire_height < 3:
            continue
        occupied_above = any(
            (px, py, pz) in grid
            for px in range(x - 1, x + 2)
            for py in range(y - 1, y + 2)
            for pz in range(base_z + 1, base_z + spire_height + 1)
        )
        if not occupied_above:
            candidates.append((x, y, base_z, spire_height))
    if not candidates:
        return

    center_x = sum(x for x, _, _, _ in candidates) / len(candidates)
    center_y = sum(y for _, y, _, _ in candidates) / len(candidates)
    first = max(candidates, key=lambda p: (p[2], (p[0] - center_x) ** 2 + (p[1] - center_y) ** 2))
    selected = [first]
    while len(selected) < 3:
        remaining = [p for p in candidates if p not in selected]
        if not remaining:
            break
        next_spire = max(
            remaining,
            key=lambda p: (
                min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in selected),
                p[2],
            ),
        )
        selected.append(next_spire)

    for x, y, base_z, spire_height in selected:
        spec["parts"].append({
            "shape": "column",
            "at": [x, y],
            "z": [base_z + 1, base_z + min(3, spire_height - 2)],
            "material": material,
            "_generated_feature": "gothic_spire",
        })
        spec["parts"].append({
            "shape": "pyramid",
            "from": [x - 1, y - 1, base_z + min(3, spire_height - 2) + 1],
            "to": [x + 1, y + 1],
            "material": material,
            "_generated_feature": "gothic_spire",
        })
    spec["gothic_spires_added"] = len(selected)


def parse_allowed_colors(value):
    colors = [color.strip() for color in value.split(",")]
    if not colors or any(not _is_hex_color(color) for color in colors):
        raise argparse.ArgumentTypeError(
            "allowed colors must be comma-separated #RRGGBB hex values"
        )
    return list(dict.fromkeys(color.lower() for color in colors))


# ---------------------------------------------------------------- game-facing inputs
# The five cutouts the game fills in: world lore, regional lore, colors, archetype, POI.

@dataclass(frozen=True)
class WorldLore:
    """How the world looks as a whole. Shared by every region in it."""

    name: str = ""
    appearance: str = ""       # landscape, sky, light, age -- what the world looks like
    architecture: str = ""     # dominant construction style across the world
    palette: tuple = ()        # #RRGGBB defaults, used when a region supplies none

    def prompt_block(self):
        return _lines(
            ("World", self.name),
            ("World appearance", self.appearance),
            ("World architecture", self.architecture),
        )


@dataclass(frozen=True)
class RegionLore:
    """How this region looks. Overrides the world wherever the two differ."""

    name: str = ""
    appearance: str = ""       # terrain, vegetation, weather, light
    culture: str = ""          # who builds here, and why
    materials: tuple = ()      # what is locally available to build with
    palette: tuple = ()        # #RRGGBB; wins over the world palette

    def prompt_block(self):
        return _lines(
            ("Region", self.name),
            ("Region appearance", self.appearance),
            ("Region culture", self.culture),
            ("Local building materials", ", ".join(self.materials)),
        )


@dataclass(frozen=True)
class POI:
    """One point of interest, as the game's world generator describes it.

    Shape-relevant fields reach the prompt; bookkeeping fields (uid, structure_id,
    at, icon) only seed the RNG so the same POI always compiles to the same model.
    """

    name: str = ""
    archetype: str = ""
    scale: str = ""            # lone, cluster, sprawling ...
    function: str = ""         # what it is for
    condition: str = ""        # pristine, ruined, flooded ...
    verticality: str = ""      # elevated, sunken, level ...
    traits: tuple = ()
    level: int | None = None
    danger: int | None = None
    difficulty: float | None = None
    region: str = ""
    from_outside: str = ""       # how it reads on approach -- the strongest visual signal
    carries_to_road: tuple = ()  # sound/smell/sight lines; mood, not geometry
    rooms: int | None = None
    passages: int | None = None
    items: int | None = None
    npcs: int | None = None    # parsed for completeness; never reaches the prompt
    uid: str = ""
    structure_id: str = ""
    icon: str = ""
    at: tuple = ()

    def prompt_block(self):
        size_hint = ""
        if self.rooms or self.passages:
            counts = ", ".join(
                f"{value} {label}" for value, label in
                ((self.rooms, "room(s)"), (self.passages, "passage(s)")) if value
            )
            size_hint = f"{counts} -- the exterior must look big enough to hold them"
        threat = ""
        if self.level is not None or self.danger is not None:
            threat = ", ".join(
                f"{label} {value}" for value, label in
                ((self.level, "level"), (self.danger, "danger")) if value is not None
            ) + " (higher means larger, more fortified, more menacing)"
        return _lines(
            ("Point of interest", self.name),
            ("Archetype", self.archetype),
            ("Scale", self.scale),
            ("Function", self.function),
            ("Condition", self.condition),
            ("Verticality", self.verticality),
            ("Traits", ", ".join(self.traits)),
            ("Threat", threat),
            ("Region", self.region),
            ("Seen from outside", self.from_outside),
            ("Carries to the road", " ".join(self.carries_to_road)),
            ("Interior", size_hint),
        )

    def seed(self):
        """Stable across runs and machines, unlike hash()."""
        key = self.structure_id or self.uid or f"{self.name}:{self.at}"
        return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")

    @classmethod
    def from_dict(cls, data):
        """Accept a loose dict from the game; unknown keys are ignored."""
        names = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in names}
        for key in ("traits", "carries_to_road", "at", "materials"):
            if isinstance(known.get(key), str):
                known[key] = _split_list(known[key])
            elif known.get(key) is not None and key in known:
                known[key] = tuple(known[key])
        known["archetype"] = str(known.get("archetype", "")).strip().lower()
        return cls(**known)

    @classmethod
    def from_text(cls, text):
        """Parse the generator's POI block: plain `Key: value` lines or `**Key:**` Markdown."""
        head, sections = _split_poi_sections(text)
        meta, title = {}, ""
        for line in head.splitlines():
            match = _POI_META_RE.match(line)
            if match:
                meta[match.group(1).strip().lower()] = match.group(2).strip()
            elif not title and line.strip():
                title = line.strip().lstrip("#").strip().strip("*").strip()
        ids = dict(re.findall(r"(\w+)\s*=\s*([\w.-]+)", meta.get("poi", "")))
        counts = {label.lower(): int(value) for value, label in
                  re.findall(r"(\d+)\s+(room|passage|item|npc)", sections.get("inside", ""), re.I)}
        return cls(
            name=title or meta.get("name", ""),
            archetype=meta.get("archetype", "").lower(),
            scale=meta.get("scale", ""),
            function=meta.get("function", ""),
            condition=meta.get("condition", ""),
            verticality=meta.get("verticality", ""),
            traits=_split_list(meta.get("traits", "")),
            level=_as_number(meta.get("level")),
            danger=_as_number(meta.get("danger")),
            difficulty=_as_number(meta.get("difficulty")),
            region=meta.get("region", ""),
            from_outside=sections.get("from outside", ""),
            carries_to_road=tuple(
                line.strip() for line in sections.get("carries to the road", "").splitlines() if line.strip()
            ),
            rooms=counts.get("room"), passages=counts.get("passage"),
            items=counts.get("item"), npcs=counts.get("npc"),
            uid=ids.get("uid", ""), structure_id=ids.get("structure", ""),
            icon=meta.get("icon", ""),
            at=_split_list(meta.get("at", "")),
        )


_POI_META_RE = re.compile(r"^\s*\*{0,2}([A-Za-z][A-Za-z /]*?)\*{0,2}\s*:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$")
_POI_SECTIONS = ("from outside", "carries to the road", "inside")


def _lines(*pairs):
    return "\n".join(f"{label}: {value}" for label, value in pairs if value not in (None, "", ()))


def _split_list(value):
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _as_number(value):
    try:
        return int(value) if float(value).is_integer() else float(value)
    except (TypeError, ValueError):
        return None


def _poi_section_key(line):
    bare = line.strip().lstrip("#").strip().strip("*").strip().lower()
    return next((name for name in _POI_SECTIONS if bare == name or bare.startswith(name + ":")), None)


def _split_poi_sections(text):
    """-> (header block, {section name: body}). Section headers may or may not be Markdown."""
    head, sections, current = [], {}, None
    for line in text.splitlines():
        key = _poi_section_key(line)
        if key:
            current = key
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
        else:
            head.append(line)
    return "\n".join(head), {k: "\n".join(v).strip() for k, v in sections.items()}


def parse_poi_description(text):
    """Back-compat shim returning the old dict shape, now built from POI.from_text."""
    poi = POI.from_text(text)
    return {"name": poi.name, "archetype": poi.archetype, "description": poi.prompt_block()}

def _repair(part):
    """Fix common LLM slips: 2-value 'at' on round shapes, unknown shapes that are really boxes."""
    part = dict(part)
    if part.get("shape") in ("cylinder", "sphere") and len(part.get("at", ())) == 2:
        part["at"] = [*part["at"], 0]
    if part.get("shape") not in ("box", "hollow_box", "column", "pyramid", "sphere", "wedge", "cylinder", "erase")             and "from" in part and "to" in part:
        part["shape"] = "box"
    if "r" in part:
        part["r"] = max(int(round(part["r"] - 0.01)), 0)  # 0.5 -> 0 = single column
    if part.get("shape") == "column" and len(part.get("at", ())) == 3:
        part["at"] = part["at"][:2]
    for k in ("from", "to"):
        if k in part and len(part[k]) == 3:
            part[k] = [part[k][0], part[k][1], max(part[k][2], 0)]
    return part


def compile_spec(spec, size=MAX_SIZE, theme=None):
    """Parts -> {(x,y,z): material}. Clamps bounds, binds slots, fixes materials, drops floating pieces."""
    theme = theme or get_theme(spec.get("theme"))
    grid = {}
    for part in spec.get("parts", []):
        mat = resolve_material(part.get("material", "primary_wall"), theme)
        part = _repair(part)
        try:
            cells = list(_cells(part))
        except (KeyError, TypeError, ValueError):
            print(f"  skipped malformed part: {part}")
            continue
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
    if not _is_template_spec(spec):
        stripped = _strip_ground_pad(grid, size)
        if stripped:
            spec["stripped_ground_pad"] = stripped
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


def _is_template_spec(spec):
    """Hand-made cave/natural specs are organic terrain on purpose, so they keep their skirt.

    Specs written before the flag existed are recognised by the short prompt finish_spec gave them.
    """
    return bool(spec.get("template")) or bool(
        re.fullmatch(r"\w+ \((?:%s)\)" % "|".join(TEMPLATE_KINDS), str(spec.get("prompt", "")))
    )


def _strip_ground_pad(grid, size=MAX_SIZE):
    """Remove a self-made ground plate: short columns the outside world can walk onto.

    The game lays its own terrain under the model, so a paved lot around it reads as a
    floating slab. Columns at most 2 voxels tall and outside the structure's silhouette
    are that pad; the structure's own floor is enclosed by taller walls and survives.
    A handful of such columns is a step or rubble, so only a sizable apron is stripped.
    """
    tops = {}
    for x, y, z in grid:
        tops[(x, y)] = max(z, tops.get((x, y), -1))
    tall = {cell for cell, top in tops.items() if top >= 2}
    in_bounds = lambda c: 0 <= c[0] < size[0] and 0 <= c[1] < size[1]
    outside, stack = set(), [(x, y) for x in range(size[0]) for y in range(size[1])
                             if (x in (0, size[0] - 1) or y in (0, size[1] - 1)) and (x, y) not in tall]
    while stack:
        cell = stack.pop()
        if cell in outside:
            continue
        outside.add(cell)
        x, y = cell
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if in_bounds(n) and n not in tall and n not in outside:
                stack.append(n)
    pad = [cell for cell in outside if cell in tops]
    if len(pad) < 12:
        return 0
    for x, y in pad:
        for z in range(tops[(x, y)] + 1):
            grid.pop((x, y, z), None)
    return len(pad)


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
        colors[x, y, z] = COLORS.get(m, m if _is_hex_color(m) else "#999999")
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
    """Tile every render in out_dir into _sheet.png with a small margin."""
    from PIL import Image, ImageChops, ImageOps
    ims = []
    for p in sorted(Path(out_dir).glob("*.png")):
        if p.name.startswith("_"):
            continue
        im = Image.open(p).convert("RGB")
        bbox = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
        ims.append(ImageOps.expand(im.crop(bbox), border=8, fill="white") if bbox else im)
    if not ims:
        return
    cols = min(cols, len(ims))
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


def llm_complete(system, user, seed=None):
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
            **({"seed": int(seed) % 2**31} if seed is not None else {}),
        )
        return r.choices[0].message.content
    if provider == "ollama":
        import urllib.request
        body = json.dumps({"model": os.environ.get("OLLAMA_MODEL", "llama3.1"), "stream": False, "format": "json",
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", body, {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())["message"]["content"]
    raise ValueError(f"unknown provider {provider}")


def _request_spec(user, seed=None):
    """LLM -> spec dict. One retry, because a malformed reply is cheaper to redo than to salvage."""
    for attempt in range(2):
        try:
            return json.loads(llm_complete(SYSTEM_PROMPT, user, seed=seed))
        except json.JSONDecodeError:
            if attempt:
                raise
            print("  LLM returned malformed JSON; retrying once")


def theme_prompt(theme_name):
    t = get_theme(theme_name)
    m = t["massing_modifiers"]
    prompt = (f"Theme: {theme_name}. Style: {t['style']}. Verticality x{m['verticality_scale']} "
              f"(taller than usual if >1, squatter if <1). "
              f"Overhangs/cantilevers: {int(m['overhang_probability'] * 100)}% likely.")
    if theme_name == "gothic":
        prompt += (
            " Required silhouette: at least three distinct, tall, narrow spires visibly rise above "
            "the main roof; build each as a shaft with a pointed cap."
        )
    return prompt


def build_generation_prompt(archetype=None, description=None, regional_lore=None, world_lore=None,
                            poi=None, region=None, world=None,
                            kind=None, mood=None, recipe=None, theme=DEFAULT_THEME, allowed_colors=None):
    """Assemble the user prompt from the cutouts, widest context first and the POI last.

    world / region take WorldLore and RegionLore; world_lore / regional_lore are the
    free-text equivalents kept for the CLI. poi takes a POI.
    """
    # Theme frame comes first so the LLM reads every POI/archetype/lore detail through
    # that visual lens, not anchored to the POI's native aesthetic (e.g. cyberpunk dungeon
    # should read as a cyberpunk structure, not a fantasy one with a cyberpunk label at the end).
    fields = [theme_prompt(theme)]
    context = [
        world.prompt_block() if world else "",
        f"World lore: {world_lore}" if world_lore else "",
        region.prompt_block() if region else "",
        f"Regional lore: {regional_lore}" if regional_lore else "",
        poi.prompt_block() if poi else "",
        f"Archetype: {archetype}" if archetype and not poi else "",
        f"Model description: {description}" if description else "",
    ]
    fields += [block for block in context if block]
    explicit_colors = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", "\n".join(fields))))
    if explicit_colors:
        fields.append(
            f"Explicit hex colors: {', '.join(explicit_colors)}. Use each exact color on the part it describes."
        )
    if kind:
        fields.append(f"Kind: {kind} ({KIND_HINTS[kind]}).")
    if mood:
        fields.append(f"Mood: {mood}.")
    if recipe:
        fields.append(f"Archetype recipe (required silhouette): {recipe}")
    fields.append("Use the slots primary_wall, roof, trim, accent.")
    if allowed_colors:
        fields.append(
            f"Allowed colors: {', '.join(allowed_colors)}. Use only these exact hex colors for every part."
        )
    return "\n".join(fields)


def _slug(text, limit=40):
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:limit] or "model"


def _resolve_palette(palette, region, world):
    """Explicit palette wins, then the region's, then the world's."""
    colors = tuple(palette or (region.palette if region else ()) or (world.palette if world else ()))
    invalid = [color for color in colors if not _is_hex_color(color)]
    if invalid:
        raise ValueError(f"palette must be #RRGGBB hex values; got {invalid}")
    return list(dict.fromkeys(color.lower() for color in colors))


def generate_voxel_object(poi=None, *, archetype=None, description=None, region=None, world=None,
                          palette=None, theme=DEFAULT_THEME, seed=None, name=None,
                          out_dir=None, render=True):
    """The game's entry point. One object from the five cutouts.

    poi         POI, a dict, or the generator's raw POI text block. Supplies archetype,
                traits, condition, verticality, scale, threat, interior size, and the
                "From outside" description. uid/structure_id seed the RNG, so the same
                POI always compiles to the same model.
    region      RegionLore: how this region looks, who builds here, local materials, palette.
    world       WorldLore: how the world looks overall and what it is built from.
    palette     list of #RRGGBB; every voxel is restricted to it. Falls back to
                region.palette, then world.palette.
    archetype   one of ARCHETYPE_ROOM_PROFILE; defaults to the POI's archetype.
    description free-text override for a one-off object.

    Returns {"name", "summary", "plan", "spec", "grid", "prompt"}. "plan" is the
    VoxelObjectPlan JSON; "grid" is {(x,y,z): material}, ready for blit().
    Files are written only when out_dir is given.
    """
    import random
    if isinstance(poi, str):
        poi = POI.from_text(poi)
    elif isinstance(poi, dict):
        poi = POI.from_dict(poi)
    archetype = (archetype or (poi.archetype if poi else "")).strip().lower() or None
    if archetype not in ARCHETYPE_ROOM_PROFILE:
        archetype = None
    palette = _resolve_palette(palette, region, world)
    if seed is None and poi:
        seed = poi.seed()
    rng = random.Random(seed)
    name = _slug(name or (poi.name if poi else "") or archetype or description)

    profile = ARCHETYPE_ROOM_PROFILE.get(archetype)
    kind = rng.choice(profile["kinds"]) if profile else None
    if kind in TEMPLATE_KINDS and not (poi or description or region or world):
        spec = rng.choice(TEMPLATES[kind])(rng)
        spec["template"] = True
        return finish_spec(spec, f"{archetype} ({kind})", name, out_dir, theme, palette, render=render)

    user = build_generation_prompt(
        archetype=archetype,
        description=description,
        poi=poi,
        region=region,
        world=world,
        kind=kind,
        mood=", ".join(profile["tags"]) if profile else None,
        recipe=ARCHETYPE_RECIPES.get(archetype),
        theme=theme,
        allowed_colors=palette,
    )
    return finish_spec(_request_spec(user, seed), user, name, out_dir, theme, palette, render=render)


generate = generate_voxel_object  # the game calls voxgen.generate(...); llm_complete is the transport


def generate_archetype(name, out_dir, rng=None, theme=DEFAULT_THEME, description=None,
                       regional_lore=None, world_lore=None, allowed_colors=None, output_name=None):
    """CLI path: archetype plus optional free-text lore -> files on disk."""
    base = name if name in ARCHETYPE_ROOM_PROFILE else name.rsplit("_", 1)[0]
    return generate_voxel_object(
        archetype=base,
        description=description,
        region=RegionLore(appearance=regional_lore) if regional_lore else None,
        world=WorldLore(appearance=world_lore) if world_lore else None,
        palette=allowed_colors,
        theme=theme,
        seed=rng.getrandbits(32) if rng else None,
        name=output_name or name,
        out_dir=out_dir,
    )


def generate_object(user, name, out_dir, theme=DEFAULT_THEME, allowed_colors=None, seed=None):
    """Pre-assembled prompt -> spec -> compiled plan + PNG."""
    return finish_spec(_request_spec(user, seed), user, name, out_dir, theme, allowed_colors)


def finish_spec(spec, user, name, out_dir, theme, allowed_colors=None, render=True):
    """Spec -> compiled plan. Writes .spec.json/.json/.png only when out_dir is given."""
    spec["prompt"] = user
    spec["theme"] = theme
    if allowed_colors:
        spec["allowed_colors"] = list(allowed_colors)
    t = get_theme(theme)
    _apply_explicit_slot_colors(spec, user, t)
    _apply_allowed_colors(spec, allowed_colors)
    _add_gothic_spires(spec, theme, allowed_colors)
    grid = compile_spec(spec, theme=t)
    spec["compiled_voxels"] = len(grid)
    plan = to_plan(spec.get("summary", name), grid, t)
    result = {"name": name, "summary": plan["brief_summary"], "plan": plan,
              "spec": spec, "grid": grid, "prompt": user}
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.spec.json").write_text(json.dumps(spec, indent=2))
        (out_dir / f"{name}.json").write_text(json.dumps(plan, indent=2))
        if render:
            render_png({c: t["palette_override"].get(m, m) for c, m in grid.items()},
                       out_dir / f"{name}.png", name)
        pad = spec.get("stripped_ground_pad")
        print(f"{name}: {len(grid)} voxels{f' (stripped a {pad}-voxel ground pad)' if pad else ''}"
              f" -> {out_dir / name}.json/.png")
    return result

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
    ap = argparse.ArgumentParser(
        description="Generate a voxel model, JSON plan, and PNG preview.",
        epilog="Use --out to choose the output folder; the default is outputs/objects.",
    )
    ap.add_argument("archetype", nargs="?", choices=sorted(ARCHETYPE_ROOM_PROFILE),
                    help="model archetype")
    ap.add_argument("--archetype", dest="archetype_option", choices=sorted(ARCHETYPE_ROOM_PROFILE),
                    help="categorical archetype (named form)")
    ap.add_argument("--all", action="store_true", help="generate every archetype")
    ap.add_argument("-p", "--prompt", "--description", dest="description",
                    help="model description; can be combined with lore and an archetype")
    ap.add_argument("--regional-lore", help="regional lore that should inform the model")
    ap.add_argument("--world-lore", help="world lore that should inform the model")
    ap.add_argument("--poi", type=Path, metavar="POI_FILE",
                    help="generate from a point-of-interest block (plain `Key: value` or Markdown)")
    ap.add_argument("--lore", type=Path, metavar="JSON_FILE",
                    help='JSON with optional "world" and "region" objects (name, appearance, architecture/culture, materials, palette)')
    ap.add_argument("--allowed-colors", type=parse_allowed_colors, metavar="#RRGGBB,...",
                    help="comma-separated hex colors; generated voxels are restricted to this palette")
    ap.add_argument("--name", help="output name (defaults to the description or POI title)")
    ap.add_argument("-o", "--out", default="outputs/objects",
                    help="output directory (default: outputs/objects)")
    ap.add_argument("-n", type=int, default=1, help="variants per archetype")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--theme", default=DEFAULT_THEME, choices=sorted(THEMES))
    ap.add_argument("--pick", nargs=3, type=int, metavar=("WORLD_SEED", "CX", "CY"),
                    help="deterministically pick a pooled spec for ARCHETYPE and blit it onto test terrain")
    ap.add_argument("--sheet", action="store_true", help="only rebuild _sheet.png from existing renders")
    ap.add_argument("--rerender", action="store_true", help="rebuild PNGs + sheet from existing .json (no LLM)")
    args = ap.parse_args()
    if args.archetype and args.archetype_option:
        ap.error("provide the archetype either positionally or with --archetype, not both")
    archetype = args.archetype_option or args.archetype
    world = region = None
    if args.lore:
        try:
            lore = json.loads(args.lore.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            ap.error(f"cannot read lore file {args.lore}: {exc}")
        world = WorldLore(**lore["world"]) if lore.get("world") else None
        region = RegionLore(**lore["region"]) if lore.get("region") else None
    if world is None and args.world_lore:
        world = WorldLore(appearance=args.world_lore)
    if region is None and args.regional_lore:
        region = RegionLore(appearance=args.regional_lore)
    poi = None
    if args.poi:
        try:
            poi = POI.from_text(args.poi.read_text(encoding="utf-8"))
        except OSError as exc:
            ap.error(f"cannot read POI description {args.poi}: {exc}")
        if args.all:
            ap.error("--poi cannot be combined with --all")
        if not poi.name:
            poi = dataclasses.replace(poi, name=args.poi.stem)
        if not archetype and poi.archetype in ARCHETYPE_ROOM_PROFILE:
            archetype = poi.archetype
    description = args.description
    if args.all and archetype:
        ap.error("--all cannot be combined with an archetype")
    if args.pick:
        sys.exit(demo_pick(args.out, args.pick[0], args.pick[1], args.pick[2], archetype or "", args.theme))
    if args.rerender:
        for p in Path(args.out).glob("*.json"):
            if not p.name.endswith(".spec.json"):
                spec_path = p.with_name(f"{p.stem}.spec.json")
                if spec_path.exists():
                    spec = json.loads(spec_path.read_text())
                    theme = spec.get("theme", DEFAULT_THEME)
                    _apply_explicit_slot_colors(spec, spec.get("prompt", ""), get_theme(theme))
                    allowed_colors = args.allowed_colors or spec.get("allowed_colors")
                    _apply_allowed_colors(spec, allowed_colors)
                    _add_gothic_spires(spec, theme, allowed_colors)
                    grid = compile_spec(spec)
                    spec["compiled_voxels"] = len(grid)
                    if spec.get("stripped_ground_pad"):
                        print(f"  {p.stem}: stripped a {spec['stripped_ground_pad']}-voxel ground pad")
                    spec_path.write_text(json.dumps(spec, indent=2))
                    plan = to_plan(spec.get("summary", p.stem), grid, get_theme(spec.get("theme")))
                    p.write_text(json.dumps(plan, indent=2))
                else:
                    plan = json.loads(p.read_text())
                    grid = {(v["x"], v["y"], v["z"]): v["material"] for v in plan["voxels"]}
                render_png(grid, p.with_suffix(".png"), p.stem)
    if (poi or description or region or world) and not args.all:
        base_name = args.name or _slug((poi.name if poi else "") or description or archetype)
        seed = args.seed if args.seed is not None else (poi.seed() if poi else None)
        for i in range(args.n):
            generate_voxel_object(
                poi,
                archetype=archetype,
                description=description,
                region=region,
                world=world,
                palette=args.allowed_colors,
                theme=args.theme,
                seed=None if seed is None else seed + i,
                name=base_name if args.n == 1 else f"{base_name}_{i}",
                out_dir=Path(args.out),
            )
        contact_sheet(args.out)
        sys.exit()
    if args.sheet or args.rerender:
        sys.exit(contact_sheet(args.out))
    names = sorted(ARCHETYPE_ROOM_PROFILE) if args.all else [archetype]
    if names == [None]:
        sys.exit(ap.print_help())
    import random
    rng = random.Random(args.seed)
    for n in names:
        for i in range(args.n):
            output_name = args.name or n
            if args.n > 1:
                output_name = f"{output_name}_{i}"
            generate_archetype(
                n if args.n == 1 else f"{n}_{i}", Path(args.out), rng, args.theme,
                description, args.regional_lore, args.world_lore, args.allowed_colors,
                output_name=output_name,
            )
    contact_sheet(args.out)
