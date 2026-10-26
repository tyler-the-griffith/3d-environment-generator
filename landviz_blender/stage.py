"""Lighting, cameras, render settings, and outputs (stills, animation frames, glTF, .blend)."""
from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector

from .util import log, look_at

# ---------------------------------------------------------------- environment


def setup_world(env: dict, project_dir: Path):
    s = bpy.context.scene
    world = bpy.data.worlds.new("Sky")
    s.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    az, el = env["sun_azimuth_site_deg"], env["sun_elevation_deg"]
    cloud = env.get("cloudiness", 0.0)
    if env.get("sky") == "hdri" and env.get("hdri"):
        p = Path(env["hdri"])
        p = p if p.is_absolute() else project_dir / p
        tex = nt.nodes.new("ShaderNodeTexEnvironment")
        tex.image = bpy.data.images.load(str(p))
        mapping = nt.nodes.new("ShaderNodeMapping")
        coord = nt.nodes.new("ShaderNodeTexCoord")
        mapping.inputs["Rotation"].default_value = (0, 0, math.radians(env.get("hdri_rotation", 0)))
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs[0], tex.inputs["Vector"])
        nt.links.new(tex.outputs[0], bg.inputs[0])
        bg.inputs["Strength"].default_value = env.get("hdri_strength", 1.0)
        return
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(max(el, -5))
    sky.sun_rotation = math.radians(az)  # verified: 0 = toward site +Y, 90 = +X (clockwise bearing)
    sky.sun_disc = el > 0
    sky.sun_size = math.radians(0.545 + 2.5 * cloud)
    sky.sun_intensity = max(0.05, 1.0 - 0.85 * cloud) * env.get("sun_strength", 1.0)
    sky.aerosol_density = env.get("haze", 1.0) * (1 + 3 * cloud)
    sky.altitude = 200
    nt.links.new(sky.outputs[0], bg.inputs[0])
    bg.inputs["Strength"].default_value = 1.0
    if el <= 2:  # dusk/night: a dim blue fill so landscape lighting reads
        bg.inputs["Strength"].default_value = 3.0


# ---------------------------------------------------------------- cameras


def make_camera(view: dict, ground, res):
    cam = bpy.data.cameras.new(view["name"])
    cam.lens = view["lens"]
    cam.sensor_width = 36
    cam.clip_start = 0.05
    cam.clip_end = 2000
    if view.get("ortho"):
        cam.type = "ORTHO"
        w, h = view["ortho_extent"]
        aspect = res[0] / res[1]
        cam.ortho_scale = max(w, h * aspect) if aspect >= 1 else max(h, w / aspect)
    obj = bpy.data.objects.new(f"CAM_{view['name']}", cam)
    bpy.context.scene.collection.objects.link(obj)
    pos = Vector(view["position"])
    tgt = Vector(view["target"])
    if view.get("position_above_ground"):
        pos.z += ground.z(pos.x, pos.y)
    if view.get("target_above_ground"):
        tgt.z += ground.z(tgt.x, tgt.y)
    pos.z = max(pos.z, ground.z(pos.x, pos.y) + 0.3)
    obj.location = pos
    if view.get("pano"):
        cam.type = "PANO"
        cam.panorama_type = "EQUIRECTANGULAR"
        obj.rotation_euler = (math.pi / 2, 0, 0)  # horizon level, image center faces site +Y
    elif view.get("ortho"):
        obj.rotation_euler = (0, 0, 0)
    else:
        look_at(obj, tgt)
    if view.get("dof"):
        cam.dof.use_dof = True
        cam.dof.focus_distance = (tgt - pos).length
        cam.dof.aperture_fstop = float(view["dof"])
    anim = view.get("animation")
    if anim:
        _animate(obj, anim, ground)
    return obj


def _animate(obj, anim, ground):
    s = bpy.context.scene
    frames = int(anim["seconds"] * anim["fps"])
    # The frame range is scene-global; store it per camera and apply it when rendering.
    obj["lv_fps"], obj["lv_frames"] = anim["fps"], frames
    target = bpy.data.objects.new(obj.name + "_target", None)
    s.collection.objects.link(target)
    con = obj.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    if anim["type"] == "orbit":
        cx, cy = anim["center"]
        gz = ground.z(cx, cy)
        target.location = (cx, cy, gz + 1.0)
        for f in range(1, frames + 1):
            a = math.radians(anim["start_azimuth"] + anim["degrees"] * (f - 1) / max(1, frames - 1))
            x, y = cx + anim["radius"] * math.sin(a), cy + anim["radius"] * math.cos(a)
            obj.location = (x, y, gz + anim["height"])
            obj.keyframe_insert("location", frame=f)
    else:  # flythrough along a smoothed path, looking ahead
        pts = [Vector(p) for p in anim["path"]]
        for _ in range(anim.get("smooth", 3)):
            if len(pts) < 3:
                break
            new = [pts[0]]
            for a, b in zip(pts, pts[1:]):
                new += [a.lerp(b, 0.25), a.lerp(b, 0.75)]
            new.append(pts[-1])
            pts = new
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + (b - a).length)
        total = cum[-1]

        def at(d):
            if d > total:  # past the end: keep looking along the final heading
                last = (pts[-1] - pts[-2]).normalized() if len(pts) > 1 else Vector((0, 1, 0))
                return pts[-1] + last * (d - total)
            d = max(0.0, d)
            for i in range(len(pts) - 1):
                if cum[i + 1] >= d:
                    t = (d - cum[i]) / max(1e-9, cum[i + 1] - cum[i])
                    return pts[i].lerp(pts[i + 1], t)
            return pts[-1]

        for f in range(1, frames + 1):
            u = (f - 1) / max(1, frames - 1)
            u = u * u * (3 - 2 * u)  # ease in/out
            d = u * total
            p = at(d)
            q = at(d + anim["look_ahead"])
            obj.location = (p.x, p.y, p.z + ground.z(p.x, p.y))
            obj.keyframe_insert("location", frame=f)
            target.location = (q.x, q.y, q.z * 0.85 + ground.z(q.x, q.y))
            target.keyframe_insert("location", frame=f)
    for fc in (obj.animation_data.action.fcurves if hasattr(obj.animation_data.action, "fcurves") else []):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


# ---------------------------------------------------------------- render settings


def configure_render(r: dict, device: str):
    s = bpy.context.scene
    s.render.engine = "CYCLES"
    cy = s.cycles
    used = "CPU"
    if device in ("gpu", "optix", "cuda"):
        prefs = bpy.context.preferences.addons["cycles"].preferences
        order = {"gpu": ("OPTIX", "CUDA"), "optix": ("OPTIX",), "cuda": ("CUDA",)}[device]
        for backend in order:
            try:
                prefs.compute_device_type = backend
                prefs.refresh_devices()
            except TypeError:
                continue
            devs = [d for d in prefs.devices if d.type == backend]
            if devs:
                for d in prefs.devices:
                    d.use = d.type == backend
                cy.device = "GPU"
                used = backend
                break
    if used == "CPU":
        cy.device = "CPU"
        if device != "cpu":
            log("no usable GPU — rendering on CPU")
    s.render.threads_mode = "AUTO"
    cy.samples = max(1, r["samples"])
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = 0.02
    cy.use_denoising = r["denoise"]
    try:
        cy.denoiser = "OPENIMAGEDENOISE"
        cy.denoising_use_gpu = used != "CPU"
    except (TypeError, AttributeError):
        pass
    cy.max_bounces = 8
    cy.diffuse_bounces = 3
    cy.glossy_bounces = 2
    cy.transmission_bounces = 4
    cy.transparent_max_bounces = 16
    cy.caustics_reflective = cy.caustics_refractive = False
    cy.seed = r.get("seed", 1)
    s.render.use_persistent_data = True
    s.render.resolution_x, s.render.resolution_y = r["resolution"]
    s.render.resolution_percentage = r["resolution_pct"]
    s.render.film_transparent = r.get("film_transparent", False)
    vs = s.view_settings
    vs.view_transform = "AgX"
    try:
        vs.look = r.get("look", "AgX - Medium High Contrast")
    except TypeError:
        pass
    vs.exposure = r.get("exposure", 0.0)
    s.render.image_settings.file_format = "PNG"
    s.render.image_settings.color_depth = "8"
    return used


def render_still(cam_obj, out_path: Path):
    s = bpy.context.scene
    s.camera = cam_obj
    s.frame_set(s.frame_start)
    s.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)


def render_frames(cam_obj, out_dir: Path):
    s = bpy.context.scene
    s.camera = cam_obj
    s.render.fps = cam_obj["lv_fps"]
    s.frame_start, s.frame_end = 1, cam_obj["lv_frames"]
    out_dir.mkdir(parents=True, exist_ok=True)
    s.render.filepath = str(out_dir / "f_")
    s.render.use_overwrite = False  # resumable: skips frames already on disk
    s.render.use_placeholder = True
    bpy.ops.render.render(animation=True)


# ---------------------------------------------------------------- export


def export_gltf(path: Path):
    """Realize all instancing (plants, leaves, grass) into real meshes and write a .glb."""
    s = bpy.context.scene
    view_layer = bpy.context.view_layer
    horizon = bpy.data.objects.get("Horizon")
    if horizon:  # the viewer draws its own sky/fog; a 6 km plane would just z-fight
        bpy.data.objects.remove(horizon)

    def select(objs):
        bpy.ops.object.select_all(action="DESELECT")
        objs = [o for o in objs if o.name in view_layer.objects]
        for o in objs:
            o.select_set(True)
        if objs:
            view_layer.objects.active = objs[0]
        return objs

    # 1. Plant collection instances → real copies of the prototype objects.
    if select([o for o in s.objects if o.type == "EMPTY" and o.instance_type == "COLLECTION"]):
        bpy.ops.object.duplicates_make_real(use_base_parent=False, use_hierarchy=False)
    # 2. Geometry Nodes scatters + bark curves → plain meshes (convert realizes instances).
    if select([o for o in s.objects if (o.type == "MESH" and any(m.type == "NODES" for m in o.modifiers))
               or o.type == "CURVE"]):
        bpy.ops.object.convert(target="MESH")
    for mat in bpy.data.materials:
        _flatten_material(mat)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_visible=True,
                              export_apply=True, export_cameras=False, export_lights=False)
    log(f"exported {path} ({path.stat().st_size / 1e6:.1f} MB)")


def _flatten_material(mat):
    """Procedural node trees don't survive glTF; set a representative base color + roughness."""
    if not mat.use_nodes:
        return
    color = None
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeValToRGB":
            els = n.color_ramp.elements
            color = tuple(sum(e.color[i] for e in els) / len(els) for i in range(4))
            break
        if n.bl_idname == "ShaderNodeTexBrick":
            color = tuple(n.inputs["Color1"].default_value)
            break
    p = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if p is None:
        return
    if color is None and not p.inputs["Base Color"].is_linked:
        color = tuple(p.inputs["Base Color"].default_value)
    if color is not None:
        mat.diffuse_color = color
        for link in list(p.inputs["Base Color"].links):
            mat.node_tree.links.remove(link)
        p.inputs["Base Color"].default_value = color
    out = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if out:
        mat.node_tree.links.new(p.outputs[0], out.inputs["Surface"])

