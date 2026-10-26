"""Blender-side half of landviz. Runs only inside Blender's Python (imports bpy).

Consumes the resolved scene JSON produced by landviz.resolve — never project.yaml or the
plant catalogs directly — so the two halves can evolve independently.
"""
