"""Convert a 3D mesh into a voxel object."""

import argparse

import numpy as np
import trimesh


def sample_voxel_colors(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """RGBA color at each point, interpolated from the mesh's baked vertex colors."""
    vertex_colors = mesh.visual.to_color().vertex_colors
    closest, _, triangle_id = mesh.nearest.on_surface(points)
    bary = trimesh.triangles.points_to_barycentric(mesh.triangles[triangle_id], closest)
    tri_colors = vertex_colors[mesh.faces[triangle_id]].astype(float)
    return np.einsum("ij,ijk->ik", bary, tri_colors).astype(np.uint8)


def voxelate(mesh_path: str, pitch: float, out_path: str, color: bool) -> None:
    mesh = trimesh.load(mesh_path, force="mesh")
    voxel_grid = mesh.voxelized(pitch).fill()

    if out_path.lower().endswith(".binvox"):
        voxel_grid.export(out_path)
    elif color and hasattr(mesh.visual, "to_color"):
        colors = sample_voxel_colors(mesh, voxel_grid.points)
        color_grid = np.zeros(voxel_grid.shape + (4,), dtype=np.uint8)
        color_grid[tuple(voxel_grid.sparse_indices.T)] = colors
        voxel_grid.as_boxes(colors=color_grid).export(out_path)
    else:
        voxel_grid.as_boxes().export(out_path)

    print(f"{mesh_path} -> {out_path} ({voxel_grid.shape} voxels @ pitch={pitch})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", help="input mesh file (.obj, .stl, .glb, ...)")
    parser.add_argument("-o", "--out", default="voxels.ply", help="output voxel mesh file")
    parser.add_argument("-p", "--pitch", type=float, default=0.1, help="voxel edge length")
    parser.add_argument("--no-color", action="store_true", help="skip texture sampling, output plain voxels")
    args = parser.parse_args()

    voxelate(args.mesh, args.pitch, args.out, color=not args.no_color)
