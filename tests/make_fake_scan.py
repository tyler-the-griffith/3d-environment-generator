# Builds projects/scantest/derived/fake_scan.obj from a web-quality demo build:
#   ./lv build demo --set render.quality=web
#   blender -b projects/demo/derived/scene.blend -P tests/make_fake_scan.py -- $PWD/projects/scantest/derived/fake_scan.obj
# The scene is joined, rotated 30° and offset (500, 200, 50) m like raw photogrammetry output.
import bpy, math, sys
from mathutils import Matrix
out = sys.argv[-1]
s = bpy.context.scene
h = bpy.data.objects.get("Horizon")
if h: bpy.data.objects.remove(h)
vl = bpy.context.view_layer
def sel(objs):
    bpy.ops.object.select_all(action="DESELECT")
    objs=[o for o in objs if o.name in vl.objects]
    for o in objs: o.select_set(True)
    if objs: vl.objects.active=objs[0]
    return objs
if sel([o for o in s.objects if o.type=="EMPTY" and o.instance_type=="COLLECTION"]):
    bpy.ops.object.duplicates_make_real()
if sel([o for o in s.objects if (o.type=="MESH" and any(m.type=="NODES" for m in o.modifiers)) or o.type=="CURVE"]):
    bpy.ops.object.convert(target="MESH")
# Keep only house, garage, terrain, trees (drop lawns/beds/paving — a scan sees it all as one surface anyway)
keep = [o for o in s.objects if o.type=="MESH" and o.name in vl.objects and (o.name.startswith(("house","garage","Terrain","plant_","street-tree","shade-tree","back-fence")))]
sel(keep)
bpy.ops.object.join()
obj = vl.objects.active
M = Matrix.Translation((500, 200, 50)) @ Matrix.Rotation(math.radians(30), 4, "Z")
obj.data.transform(obj.matrix_world)
obj.matrix_world = Matrix.Identity(4)
obj.data.transform(M)
sel([obj])
bpy.ops.wm.obj_export(filepath=out, export_selected_objects=True, forward_axis="Y", up_axis="Z", export_materials=False)
print("FAKESCAN", len(obj.data.polygons))
