"""landviz — landscape plans to 3D renders.

Core half (this package): pure Python + numpy + PyYAML. Loads project.yaml, resolves it into
a plain-JSON scene, draws 2D previews, ingests references, drives photogrammetry and Blender.
Blender half: landviz_blender/ (runs inside Blender).
"""
__version__ = "0.1.0"
