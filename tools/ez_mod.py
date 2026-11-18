# -*- coding: utf-8 -*-
import os
import sys
import json
import struct
import itertools
import bpy
import bmesh
from mathutils import Vector, kdtree

if r"Z:\aarma" not in sys.path:
    sys.path.append(r"Z:\aarma")

import ez_mod_core

MANNEQUIN_PATH = r"Z:\aarma\tools\arma3_mannequin.p3d"
FINGER_REF_PATH = r"Z:\aarma\.cache_thumbs\mannequin_b_soldier_01_mlod.p3d"
CACHE_THUMBS = r"Z:\aarma\.cache_thumbs"

REF_MODELS = {
    "HEADGEAR": (r"Z:\aarma\.cache_thumbs\ach_c_test_mlod.p3d", "Ref_Helmet_ACH"),
    "VEST": (r"Z:\aarma\.cache_thumbs\vest_spiritus_mlod.p3d", "Ref_Vest_Spiritus"),
    "BACKPACK": (r"Z:\aarma\.cache_thumbs\ares_mlod.p3d", "Ref_Backpack_Ares"),
    "NVG": (r"Z:\aarma\.cache_thumbs\armband_y_mlod.p3d", "Ref_Armband"),
}


def load_mlod_lod0_data(filepath):
    """Reads LOD 0 vertices (in Blender X, Z, Y space), faces, UVs, and bone weights from an MLOD .p3d."""
    with open(filepath, "rb") as f:
        data = f.read()

    if data[:4] != b"MLOD":
        raise RuntimeError("Not an MLOD file: " + filepath)

    pos = 12
    if data[pos : pos + 4] != b"P3DM":
        raise RuntimeError("Invalid P3DM header")

    major, minor, num_pts, num_nrm, num_faces, dummy = struct.unpack("<IIIIII", data[pos + 4 : pos + 28])
    pos += 28

    verts_bl = []
    for _ in range(num_pts):
        x, y, z, _ = struct.unpack("<fffi", data[pos : pos + 16])
        pos += 16
        # Arma (X right, Y up, Z forward) -> Blender (X right, Y forward, Z up)
        verts_bl.append((x, z, y))

    pos += num_nrm * 12

    faces = []
    face_uvs = []
    for _ in range(num_faces):
        sides = struct.unpack("<I", data[pos : pos + 4])[0]
        pos += 4
        f_v = []
        f_uv = []
        for n in range(4):
            vi, ni, u, v = struct.unpack("<iiff", data[pos : pos + 16])
            pos += 16
            if n < sides:
                f_v.append(vi)
                f_uv.append((u, 1.0 - v))
        pos += 4  # flags
        t_end = data.find(b"\x00", pos)
        pos = t_end + 1
        m_end = data.find(b"\x00", pos)
        pos = m_end + 1
        if sides >= 3 and len(set(f_v)) == sides:
            faces.append(f_v)
            face_uvs.append(f_uv)

    selections = {}
    if data[pos : pos + 4] == b"TAGG":
        pos += 4
        while pos < len(data) - 10:
            act = data[pos]
            pos += 1
            t_end = data.find(b"\x00", pos)
            if t_end == -1:
                break
            name = data[pos:t_end].decode("latin1", errors="replace")
            pos = t_end + 1
            sz = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4
            if name == "#EndOfFile#":
                break
            elif not name.startswith("#") and not name.startswith("proxy:") and sz >= num_pts:
                pt_bytes = data[pos : pos + num_pts]
                weights = {}
                for i, b in enumerate(pt_bytes):
                    if b == 1:
                        weights[i] = 1.0
                    elif b > 1:
                        weights[i] = max(0.01, (255 - b) / 254.0)
                if weights:
                    selections[name] = weights
            pos += sz

    return verts_bl, faces, face_uvs, selections


def compute_auto_weights_for_points(points_bl, gear_type, bone_mode="AUTO"):
    """
    Computes Arma 3 OFP2_ManSkeleton weights for a list of Blender coordinates (x, y, z).
    bone_mode: 'AUTO', 'MANNEQUIN_KD', or explicit bone name ('head', 'spine3', 'pelvis', etc.).
    """
    num_pts = len(points_bl)
    groups = {"camo": {i: 1.0 for i in range(num_pts)}}

    if bone_mode and bone_mode not in ("AUTO", "MANNEQUIN_KD"):
        groups[bone_mode] = {i: 1.0 for i in range(num_pts)}
        return groups

    if bone_mode == "AUTO" and gear_type in ("HEADGEAR", "GOGGLES", "FACE"):
        groups["head"] = {i: 1.0 for i in range(num_pts)}
        return groups

    if os.path.exists(MANNEQUIN_PATH):
        m_verts, _, _, m_sels = load_mlod_lod0_data(MANNEQUIN_PATH)
        kd = kdtree.KDTree(len(m_verts))
        for idx, co in enumerate(m_verts):
            kd.insert(Vector(co), idx)
        kd.balance()

        for bname in m_sels.keys():
            groups[bname] = {}

        for vi, pt in enumerate(points_bl):
            nearest = kd.find_n(Vector(pt), 6)
            # Inverse distance weighting
            total_inv = 0.0
            inv_list = []
            for co, m_idx, dist in nearest:
                inv = 1.0 / (max(dist, 0.0005) ** 2)
                inv_list.append((m_idx, inv))
                total_inv += inv

            bone_accum = {}
            for m_idx, inv in inv_list:
                factor = inv / total_inv
                for bname, bmap in m_sels.items():
                    mw = bmap.get(m_idx, 0.0)
                    if mw > 0.0:
                        bone_accum[bname] = bone_accum.get(bname, 0.0) + mw * factor

            for bname, final_w in bone_accum.items():
                if final_w > 0.02:
                    groups[bname][vi] = min(1.0, final_w)

        # Clean empty groups
        groups = {k: v for k, v in groups.items() if v}
        return groups

    # Fallback if mannequin file not present
    if gear_type in ("VEST", "BACKPACK"):
        groups["spine3"] = {i: 1.0 for i in range(num_pts)}
    else:
        groups["leftarm"] = {i: 1.0 for i in range(num_pts)}
    return groups


def extract_slot_textures(mat):
    """Extracts (diffuse_path, normal_path) from a single Blender Material, or bakes its Base Color into a solid PNG."""
    if not mat:
        return None, None
    diff_p = None
    norm_p = None
    base_rgba = None

    def _resolve_img_path(img):
        if not img:
            return ""
        img_fp = bpy.path.abspath(img.filepath) if img.filepath else ""
        if (not img_fp or not os.path.exists(img_fp)) and (img.packed_file or getattr(img, "has_data", False)):
            os.makedirs(CACHE_THUMBS, exist_ok=True)
            safe_img = "".join(c if c.isalnum() else "_" for c in (img.name or mat.name))
            img_fp = os.path.join(CACHE_THUMBS, f"packed_{safe_img}.png")
            try:
                img.filepath_raw = img_fp
                img.file_format = "PNG"
                img.save()
            except Exception:
                try:
                    img.save_render(img_fp)
                except Exception:
                    img_fp = ""
        return img_fp if (img_fp and os.path.exists(img_fp)) else ""

    def _is_normal_name(nl):
        return any(k in nl for k in ("norm", "nohq", "_nrm", "nrm_", "_n.", "_n_", "_nml", "bump"))

    def _is_utility_non_diffuse_name(nl):
        if _is_diffuse_name(nl):
            return False
        return any(
            k in nl
            for k in (
                "gloss",
                "rough",
                "metal",
                "spec",
                "smdi",
                "phong",
                "exponent",
                "height",
                "disp",
                "opac",
                "mask",
                "_ao.",
                "_ao_",
                "ambient",
                "_g.",
                "_g_",
                "_r.",
                "_r_",
                "_m.",
                "_m_",
                "_s.",
                "_s_",
                "_e.",
                "_e_",
                "_orm.",
                "_orm_",
                "_arm.",
                "_rma.",
            )
        )

    def _is_diffuse_name(nl):
        return any(k in nl for k in ("diff", "albedo", "_col", "color", "basecolor", "basetexture", "_d.", "_d_", "_co.", "_co_", "_rgb"))

    def _trace_img_node(from_node, mode="color", depth=0, visited=None):
        if not from_node or depth > 8:
            return None
        if visited is None:
            visited = set()
        if from_node in visited:
            return None
        visited.add(from_node)

        if from_node.type == "TEX_IMAGE" and from_node.image:
            return from_node

        # Order input sockets so primary color/normal inputs are traced before Factor/Mask sockets
        ordered_inputs = []
        fallback_inputs = []
        for in_sock in from_node.inputs:
            if not in_sock.is_linked:
                continue
            sn = in_sock.name.lower()
            if mode == "color" and sn in ("fac", "factor", "alpha", "mask", "ao"):
                fallback_inputs.append(in_sock)
            elif mode == "color" and sn in ("color", "color1", "a", "base color", "image", "diffuse"):
                ordered_inputs.insert(0, in_sock)
            else:
                ordered_inputs.append(in_sock)

        for in_sock in ordered_inputs + fallback_inputs:
            for link in in_sock.links:
                res = _trace_img_node(link.from_node, mode=mode, depth=depth + 1, visited=visited)
                if res:
                    # For color mode, avoid accidentally picking a normal/gloss/mask map through a Mix node if a better link exists
                    if mode == "color" and res.image:
                        rnl = (res.name + " " + res.image.name + " " + os.path.basename(res.image.filepath or "")).lower()
                        if _is_normal_name(rnl) or _is_utility_non_diffuse_name(rnl):
                            continue
                    return res
        return None

    if getattr(mat, "use_nodes", False) and mat.node_tree:
        # 1. Primary path: trace directly from EZ_Arma3_Glass or Principled BSDF (or active Surface shader) inputs!
        shader_nodes = [
            n
            for n in mat.node_tree.nodes
            if n.type == "BSDF_PRINCIPLED" or (n.type == "GROUP" and n.node_tree and n.node_tree.name == "EZ_Arma3_Glass")
        ]
        if not shader_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "OUTPUT_MATERIAL" and "Surface" in n.inputs and n.inputs["Surface"].is_linked:
                    shader_nodes.append(n.inputs["Surface"].links[0].from_node)

        for node in shader_nodes:
            # Base Color -> Diffuse
            for cname in ("Base Color", "Color", "Diffuse", "Diffuse Color"):
                if cname in node.inputs:
                    c_sock = node.inputs[cname]
                    if base_rgba is None and hasattr(c_sock, "default_value"):
                        try:
                            c = c_sock.default_value
                            base_rgba = (float(c[0]), float(c[1]), float(c[2]))
                        except Exception:
                            pass
                    if not diff_p and c_sock.is_linked:
                        for link in c_sock.links:
                            img_node = _trace_img_node(link.from_node, mode="color")
                            if img_node and img_node.image:
                                fp = _resolve_img_path(img_node.image)
                                if fp:
                                    diff_p = fp
                                    break
                    break

            # Normal -> Normal Map
            if not norm_p and "Normal" in node.inputs and node.inputs["Normal"].is_linked:
                for link in node.inputs["Normal"].links:
                    img_node = _trace_img_node(link.from_node, mode="normal")
                    if img_node and img_node.image:
                        fp = _resolve_img_path(img_node.image)
                        if fp:
                            norm_p = fp
                            break

        # 2. Fallback path: scan unlinked/other TEX_IMAGE nodes, strictly skipping gloss/rough/spec maps as diffuse!
        fallback_diff = None
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                img_fp = _resolve_img_path(node.image)
                if not img_fp:
                    continue
                nl = (node.name + " " + node.image.name + " " + os.path.basename(img_fp)).lower()
                if _is_normal_name(nl):
                    if not norm_p:
                        norm_p = img_fp
                elif _is_utility_non_diffuse_name(nl):
                    continue
                elif _is_diffuse_name(nl):
                    if not diff_p:
                        diff_p = img_fp
                else:
                    if not fallback_diff:
                        fallback_diff = img_fp

        if not diff_p and fallback_diff:
            diff_p = fallback_diff

    # If the material has no diffuse image texture, generate a 64x64 solid PNG from its Base Color (e.g. black balaclava/mask)
    if not diff_p:
        if base_rgba is None and hasattr(mat, "diffuse_color"):
            try:
                dc = mat.diffuse_color
                base_rgba = (float(dc[0]), float(dc[1]), float(dc[2]))
            except Exception:
                pass
        if base_rgba is not None:
            def lin_to_srgb(v):
                v = max(0.0, min(1.0, v))
                s = v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055
                return int(round(max(0.0, min(1.0, s)) * 255.0))
            rgb = (lin_to_srgb(base_rgba[0]), lin_to_srgb(base_rgba[1]), lin_to_srgb(base_rgba[2]))
            os.makedirs(CACHE_THUMBS, exist_ok=True)
            solid_fp = os.path.join(CACHE_THUMBS, f"solid_mat_{rgb[0]}_{rgb[1]}_{rgb[2]}.png")
            if not os.path.exists(solid_fp):
                ez_mod_core.create_solid_png(solid_fp, rgb=rgb)
            diff_p = solid_fp

    return diff_p, norm_p


def _lin_to_srgb_byte(v):
    v = max(0.0, min(1.0, float(v)))
    s = v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055
    return int(round(max(0.0, min(1.0, s)) * 255.0))


def detect_glass_material_info(mat, diff_p=None):
    """
    Detects whether a Blender material Slot explicitly uses the 'EZ_Arma3_Glass' node group
    (or mat['ez_is_arma_glass'] == True).
    Never guesses by material name or random alpha so straps/balaclavas are NEVER turned into glass!
    """
    if not mat:
        return None

    has_glass_node = False
    alpha_val = 0.44
    tint_rgb = None

    if getattr(mat, "use_nodes", False) and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "GROUP" and node.node_tree and node.node_tree.name == "EZ_Arma3_Glass":
                has_glass_node = True
                if "Alpha" in node.inputs:
                    try:
                        alpha_val = float(node.inputs["Alpha"].default_value)
                    except Exception:
                        pass
                if "Base Color" in node.inputs:
                    bc_sock = node.inputs["Base Color"]
                    if not bc_sock.is_linked:
                        try:
                            c = bc_sock.default_value
                            tint_rgb = (_lin_to_srgb_byte(c[0]), _lin_to_srgb_byte(c[1]), _lin_to_srgb_byte(c[2]))
                        except Exception:
                            pass
                    else:
                        from_n = bc_sock.links[0].from_node
                        if from_n.type in ("MIX", "MIX_RGB"):
                            for in_s in from_n.inputs:
                                if not in_s.is_linked and in_s.type == "RGBA":
                                    try:
                                        c = in_s.default_value
                                        rgb_cand = (_lin_to_srgb_byte(c[0]), _lin_to_srgb_byte(c[1]), _lin_to_srgb_byte(c[2]))
                                        if rgb_cand not in ((255, 255, 255), (0, 0, 0)):
                                            tint_rgb = rgb_cand
                                    except Exception:
                                        pass
                break

    if not has_glass_node and bool(mat.get("ez_is_arma_glass")):
        has_glass_node = True

    if not has_glass_node:
        return None

    eff_alpha = alpha_val if alpha_val < 0.92 else 0.44
    eff_alpha = max(0.18, min(0.85, eff_alpha))

    return {
        "is_glass": True,
        "alpha": eff_alpha,
        "tint_rgb": tint_rgb or (255, 255, 255),
    }


def ensure_ez_arma3_glass_node_group():
    """Creates or returns the 'EZ_Arma3_Glass' ShaderNodeTree matching Arma 3 ballistic/goggle glass."""
    ng_name = "EZ_Arma3_Glass"
    ng = bpy.data.node_groups.get(ng_name)
    if ng and ng.bl_idname == "ShaderNodeTree":
        return ng

    ng = bpy.data.node_groups.new(ng_name, "ShaderNodeTree")
    iface = ng.interface
    s_col = iface.new_socket(name="Base Color", in_out="INPUT", socket_type="NodeSocketColor")
    s_col.default_value = (0.45, 0.08, 0.08, 1.0)
    s_alpha = iface.new_socket(name="Alpha", in_out="INPUT", socket_type="NodeSocketFloat")
    s_alpha.default_value = 0.44
    s_alpha.min_value = 0.05
    s_alpha.max_value = 0.95
    s_rough = iface.new_socket(name="Roughness", in_out="INPUT", socket_type="NodeSocketFloat")
    s_rough.default_value = 0.12
    s_rough.min_value = 0.0
    s_rough.max_value = 1.0
    s_ior = iface.new_socket(name="IOR", in_out="INPUT", socket_type="NodeSocketFloat")
    s_ior.default_value = 1.50
    s_ior.min_value = 1.0
    s_ior.max_value = 2.5
    iface.new_socket(name="Normal", in_out="INPUT", socket_type="NodeSocketVector")
    iface.new_socket(name="BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")

    n_in = ng.nodes.new("NodeGroupInput")
    n_in.location = (-320, 0)
    n_out = ng.nodes.new("NodeGroupOutput")
    n_out.location = (320, 0)
    bsdf = ng.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.05
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = 0.75
    if "Coat Roughness" in bsdf.inputs:
        bsdf.inputs["Coat Roughness"].default_value = 0.04
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.85

    ng.links.new(n_in.outputs["Base Color"], bsdf.inputs["Base Color"])
    ng.links.new(n_in.outputs["Alpha"], bsdf.inputs["Alpha"])
    ng.links.new(n_in.outputs["Roughness"], bsdf.inputs["Roughness"])
    if "IOR" in bsdf.inputs:
        ng.links.new(n_in.outputs["IOR"], bsdf.inputs["IOR"])
    if "Normal" in bsdf.inputs:
        ng.links.new(n_in.outputs["Normal"], bsdf.inputs["Normal"])
    ng.links.new(bsdf.outputs["BSDF"], n_out.inputs["BSDF"])
    return ng


class EZMOD_OT_make_glass_node(bpy.types.Operator):
    bl_idname = "ezmod.make_glass_node"
    bl_label = "🕶 Сделать активный материал стеклом Arma 3"
    bl_description = (
        "Заменяет шейдер ТОЛЬКО активного слота материала на ноду EZ_Arma3_Glass (не трогая балаклаву и остальные слоты). "
        "При сборке в Arma 3 этот слот получит полупрозрачность (*_ca.paa) и зеркальный блик Fresnel"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mat = getattr(context, "material", None)
        if not mat:
            obj = _resolve_target_gear_mesh(context)
            if obj:
                mat = obj.active_material
        if not mat:
            self.report({"ERROR"}, "Выберите слот материала (стекло/линзы), который нужно сделать стеклом!")
            return {"CANCELLED"}

        if not getattr(mat, "use_nodes", False):
            mat.use_nodes = True
        nt = mat.node_tree
        if not nt:
            self.report({"ERROR"}, "У материала нет дерева нодов!")
            return {"CANCELLED"}

        ng = ensure_ez_arma3_glass_node_group()

        # Check if EZ_Arma3_Glass is already present
        existing_g = next(
            (n for n in nt.nodes if n.type == "GROUP" and n.node_tree and n.node_tree.name == "EZ_Arma3_Glass"),
            None,
        )
        if existing_g:
            mat["ez_is_arma_glass"] = True
            self.report({"INFO"}, f"Материал '{mat.name}' уже использует ноду EZ_Arma3_Glass!")
            return {"FINISHED"}

        # Read existing Principled BSDF settings & links so we preserve color/texture/normal
        old_bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        out_node = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if not out_node:
            out_node = nt.nodes.new("ShaderNodeOutputMaterial")
            out_node.location = (340, 0)

        glass_node = nt.nodes.new("ShaderNodeGroup")
        glass_node.node_tree = ng
        glass_node.name = "EZ_Arma3_Glass"
        glass_node.label = "EZ_Arma3_Glass (Стекло)"
        glass_node.location = old_bsdf.location if old_bsdf else (0, 0)

        if old_bsdf:
            # Copy Base Color link or default value
            bc_in = old_bsdf.inputs.get("Base Color")
            if bc_in:
                if bc_in.is_linked:
                    for lk in list(bc_in.links):
                        nt.links.new(lk.from_socket, glass_node.inputs["Base Color"])
                else:
                    glass_node.inputs["Base Color"].default_value = tuple(bc_in.default_value)
            # Copy Alpha if < 0.92, else 0.44
            a_in = old_bsdf.inputs.get("Alpha")
            if a_in and not a_in.is_linked and float(a_in.default_value) < 0.92:
                glass_node.inputs["Alpha"].default_value = max(0.18, min(0.85, float(a_in.default_value)))
            else:
                glass_node.inputs["Alpha"].default_value = 0.44
            # Copy Roughness
            r_in = old_bsdf.inputs.get("Roughness")
            if r_in and not r_in.is_linked:
                glass_node.inputs["Roughness"].default_value = min(0.25, float(r_in.default_value))
            # Copy Normal link
            n_in = old_bsdf.inputs.get("Normal")
            if n_in and n_in.is_linked:
                for lk in list(n_in.links):
                    nt.links.new(lk.from_socket, glass_node.inputs["Normal"])
            nt.nodes.remove(old_bsdf)

        nt.links.new(glass_node.outputs["BSDF"], out_node.inputs["Surface"])
        mat["ez_is_arma_glass"] = True
        if hasattr(mat, "blend_method"):
            try:
                mat.blend_method = "BLEND"
            except Exception:
                pass

        self.report({"INFO"}, f"🕶 Слот '{mat.name}' переведён на ноду EZ_Arma3_Glass! Остальные материалы не затронуты.")
        return {"FINISHED"}


class EZMOD_PT_shader_glass_panel(bpy.types.Panel):
    bl_idname = "EZMOD_PT_shader_glass_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "EZ_MOD"
    bl_label = "🕶 EZ_MOD: Стекло Arma 3"

    @classmethod
    def poll(cls, context):
        sd = getattr(context, "space_data", None)
        return bool(sd and sd.type == "NODE_EDITOR" and getattr(sd, "tree_type", "") == "ShaderNodeTree")

    def draw(self, context):
        layout = self.layout
        mat = getattr(context, "material", None)
        if mat:
            layout.label(text=f"Слот: {mat.name}", icon="MATERIAL")
        col = layout.column(align=True)
        col.scale_y = 1.35
        col.operator("ezmod.make_glass_node", text="🕶 Добавить ноду EZ_Arma3_Glass", icon="SHADING_RENDERED")
def detect_textures_from_object(obj, props):
    """Finds diffuse and normal map file paths from EZ_MOD properties or Blender Shader nodes."""
    diff_path = bpy.path.abspath(props.diffuse_tex) if props.diffuse_tex else ""
    norm_path = bpy.path.abspath(props.normal_tex) if props.normal_tex else ""

    if diff_path and os.path.exists(diff_path):
        return diff_path, (norm_path if os.path.exists(norm_path) else None)

    for slot in obj.material_slots:
        d, n = extract_slot_textures(slot.material)
        if d and not diff_path:
            diff_path = d
        if n and not norm_path:
            norm_path = n

    return (diff_path if diff_path and os.path.exists(diff_path) else None), (
        norm_path if norm_path and os.path.exists(norm_path) else None
    )


def resolve_best_face_uvs(face, uv_layers, primary_uv):
    """
    If a mesh has multiple UV maps (e.g. after Ctrl+J joining multiple assets with different UV map names),
    a face may have (0,0) on primary_uv while its real UVs live on another UV layer.
    Returns the UV layer that actually has non-degenerate UVs for this face.
    """
    if not uv_layers:
        return [(0.0, 0.0) for _ in face.loops]

    def get_uvs(layer):
        return [(loop[layer].uv.x, loop[layer].uv.y) for loop in face.loops]

    def is_valid_uvs(uv_list):
        if not uv_list:
            return False
        us = [u for u, _ in uv_list]
        vs = [v for _, v in uv_list]
        span = (max(us) - min(us)) + (max(vs) - min(vs))
        non_zero = any(abs(u) > 1e-4 or abs(v) > 1e-4 for u, v in uv_list)
        return span > 1e-5 and non_zero

    if primary_uv:
        cand = get_uvs(primary_uv)
        if is_valid_uvs(cand):
            return cand

    for layer in uv_layers:
        if layer == primary_uv:
            continue
        cand = get_uvs(layer)
        if is_valid_uvs(cand):
            return cand

    return get_uvs(primary_uv or uv_layers[0])


class EZModAceaxValueItem(bpy.types.PropertyGroup):
    value: bpy.props.StringProperty(
        name="Вариант",
        description="Название кнопки варианта (например: Black, Olive, Multicam)",
        default="Base",
    )
    scene_snapshot: bpy.props.StringProperty(
        name="Снимок вида",
        description="Сохранённое состояние видимости объектов и материалов сцены для этого варианта",
        default="",
    )


class EZModAceaxOptionItem(bpy.types.PropertyGroup):
    option_id: bpy.props.StringProperty(
        name="ID категории",
        description="ID категории в XtdGearModels (например: Camo, Pants, Gloves, NVG)",
        default="Camo",
    )
    values: bpy.props.CollectionProperty(type=EZModAceaxValueItem)
    active_value_idx: bpy.props.IntProperty(default=0)
    preview_enabled: bpy.props.BoolProperty(default=True)


def _list_workshop_mod_items(self, context):
    items = []
    seen = set()
    wdir = ez_mod_core.WORKSHOP_DIR
    if os.path.isdir(wdir):
        try:
            for name in sorted(os.listdir(wdir), key=str.lower):
                full = os.path.join(wdir, name)
                if name.startswith("@") and os.path.isdir(full):
                    items.append((name, name, f"Папка мода {name}"))
                    seen.add(name)
        except Exception:
            pass
    if "@EZ_Custom_Gear" not in seen:
        items.insert(0, ("@EZ_Custom_Gear", "@EZ_Custom_Gear", "Стандартный пак EZ_MOD"))
    return items


def _on_mod_folder_preset_update(self, context):
    val = getattr(self, "mod_folder_preset", "")
    if val:
        self.mod_folder = val


def _list_rig_bone_items(self, context):
    items = []
    obj = _resolve_target_gear_mesh(context) if context else None
    arm = None
    if obj:
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.object:
                arm = m.object
                break
    if arm is None:
        arm = bpy.data.objects.get("Arma3_Skeleton")
    if arm and arm.type == "ARMATURE" and arm.data:
        for b in arm.data.bones:
            items.append((b.name, b.name, f"Привязать к кости {b.name}"))
    if not items:
        items.append(("head", "head", "Голова (head)"))
    return items


def _list_bridge_history_items(self, context):
    items = []
    hist_path = os.path.join(r"Z:\aarma\extracted\blender_bridge", "history.json")
    latest_path = os.path.join(r"Z:\aarma\extracted\blender_bridge", "latest_import.json")
    entries = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            entries = []
    elif os.path.exists(latest_path):
        try:
            with open(latest_path, "r", encoding="utf-8") as f:
                entries = [json.load(f)]
        except Exception:
            entries = []

    seen = set()
    for idx, m in enumerate(entries):
        mlod_p = m.get("mlod_p3d_path", "")
        if not mlod_p or mlod_p in seen or not os.path.exists(mlod_p):
            continue
        seen.add(mlod_p)
        iname = m.get("item_name") or m.get("model_stem") or f"Model_{idx}"
        gtype = m.get("gear_type", "GEAR")
        cslug = m.get("class_slug", "")
        label = f"📦 {iname} [{gtype}]"
        desc = f"ID: {cslug} | Полигонов: {m.get('face_count', 0)} | Материалов: {len(m.get('material_slots', []))}"
        items.append((mlod_p, label, desc))

    if not items:
        items.append(("NONE", "Нет выбранной модели (нажми 'В Blender' в Инспекторе или выбери .p3d)", ""))
    return items


def _list_scene_gear_meshes(self, context):
    items = []
    if context and context.scene:
        for o in context.scene.objects:
            if not _is_helper_scene_obj(o):
                n_poly = len(o.data.polygons) if o.data else 0
                n_mat = max(1, len(o.material_slots))
                items.append((o.name, f"🎯 {o.name} ({n_poly} пол. | {n_mat} мат.)", f"Выбрать 3D-модель '{o.name}'"))
    if not items:
        items.append(("NONE", "— В сцене нет 3D-моделей —", ""))
    return items


def _on_scene_gear_mesh_update(self, context):
    name = getattr(self, "scene_gear_mesh", "")
    if not name or name == "NONE" or not context:
        return
    target = bpy.data.objects.get(name)
    if target and not _is_helper_scene_obj(target):
        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            target.select_set(True)
            context.view_layer.objects.active = target
        except Exception:
            pass


class EZModProperties(bpy.types.PropertyGroup):
    scene_gear_mesh: bpy.props.EnumProperty(
        name="3D-Модель в сцене",
        description="Быстрое переключение активной 3D-модели вашей снаряги в сцене",
        items=_list_scene_gear_meshes,
        update=_on_scene_gear_mesh_update,
    )
    bridge_selected_model: bpy.props.EnumProperty(
        name="Модель из Инспектора",
        description="Выберите модель, отправленную из Arma 3 Mod Manager & 3D Inspector",
        items=_list_bridge_history_items,
    )
    sent_target_bone: bpy.props.EnumProperty(
        name="Кость для жёсткой привязки",
        description="К какой кости привязать весь объект (или выделенные вершины в Edit Mode)",
        items=_list_rig_bone_items,
    )
    mod_folder: bpy.props.StringProperty(
        name="Папка мода",
        description="Имя папки @Мода в !Workshop (если папки нет — создастся автоматически вместе с mod.cpp и addons/)",
        default="@EZ_Custom_Gear",
    )
    mod_folder_preset: bpy.props.EnumProperty(
        name="Выбрать @мод",
        description="Быстрый выбор существующей папки @Мода из !Workshop",
        items=_list_workshop_mod_items,
        update=_on_mod_folder_preset_update,
    )
    item_name: bpy.props.StringProperty(
        name="Название в игре",
        description="Имя предмета, которое будет отображаться в Арсенале Arma 3",
        default="My Custom Helmet",
    )
    class_slug: bpy.props.StringProperty(
        name="ID Класса (латиница)",
        description="Уникальный тег предмета (например: my_helmet_01)",
        default="my_custom_gear",
    )
    author_name: bpy.props.StringProperty(
        name="Автор",
        description="Ваше имя автора в Арсенале",
        default="EZ_MOD Creator",
    )
    hide_in_first_person: bpy.props.BoolProperty(
        name="Скрыть в 1-м лице",
        description="Создаёт пустой лод View Pilot, чтобы очки/шлем/снаряжение не перекрывали обзор от 1-го лица",
        default=False,
    )
    gear_type: bpy.props.EnumProperty(
        name="Тип снаряги",
        description="В какой слот персонажа Arma 3 пойдет эта модель",
        items=[
            ("HEADGEAR", "🪖 Шлем / Каска / Шапка (Headgear)", "Надевается на голову и следует за костью head"),
            ("VEST", "🦺 Бронежилет / Плитник / Разгрузка (Vest)", "Надевается на торс и гнётся со спиной и плечами"),
            ("UNIFORM", "👕 Костюм / Форма / Одежда (Uniform)", "Полный костюм/форма бойца со штанами, курткой и перчатками (Uniform_Base + B_Soldier_base_F)"),
            ("BACKPACK", "🎒 Рюкзак / Сумка (Backpack)", "Вешается за спину с инвентарём"),
            ("GOGGLES", "🥽 Маска / Очки / Балаклава (Facewear)", "Слот очков/масок на лице"),
            ("FACE", "🧑 Голова / Лицо (CfgFaces)", "Кастомная голова или лицо персонажа (CfgHeads + CfgFaces)"),
            ("NVG", "🔦 Повязка / ПНВ / Наплечник (NVG Slot)", "Слот ПНВ или нарукавной повязки"),
        ],
        default="HEADGEAR",
    )
    diffuse_tex: bpy.props.StringProperty(
        name="Текстура цвета (PNG/TGA/PAA)",
        description="Выберите PNG/JPG/TGA/PAA текстуру (или оставьте пустым, чтобы взять из материала Blender)",
        subtype="FILE_PATH",
        default="",
    )
    normal_tex: bpy.props.StringProperty(
        name="Карта рельефа Normal (опц.)",
        description="Необязательная карта нормалей PNG/TGA/PAA",
        subtype="FILE_PATH",
        default="",
    )
    armor_val: bpy.props.IntProperty(
        name="Защита (Armor)",
        description="Уровень бронезащиты шлема или плитника",
        default=12,
        min=0,
        max=200,
    )
    mass_val: bpy.props.IntProperty(
        name="Вес (Mass)",
        description="Вес предмета в инвентаре Arma 3",
        default=20,
        min=1,
        max=500,
    )
    load_val: bpy.props.IntProperty(
        name="Вместимость (Load)",
        description="Вместимость разгрузки или рюкзака",
        default=160,
        min=0,
        max=1000,
    )
    rig_bone_mode: bpy.props.EnumProperty(
        name="Кость скелета",
        description="К какой кости Arma 3 привязать выбранный ассет",
        items=[
            ("AUTO", "🤖 Авто по слоту (Голова / Торс)", "Голова для шлемов/масок, мягкий перенос с манекена для броников/рюкзаков"),
            ("head", "🪖 Голова (head)", "Жёсткая привязка к кости головы (шлемы, очки, маски)"),
            ("spine3", "🦺 Верх груди (spine3)", "Жёсткая привязка к верхней части груди"),
            ("spine2", "🦺 Центр торса (spine2)", "Жёсткая привязка к центру торса"),
            ("pelvis", "👖 Пояс / Таз (pelvis)", "Жёсткая привязка к поясу/тазу"),
            ("leftarm", "💪 Левое плечо (leftarm)", "Шеврон / повязка на левом плече"),
            ("rightarm", "💪 Правое плечо (rightarm)", "Шеврон / повязка на правом плече"),
            ("lefthand", "🖐 Левая кисть (lefthand)", "Часы / перчатка / предмет в левой руке"),
            ("righthand", "🖐 Правая кисть (righthand)", "Предмет в правой руке"),
            ("MANNEQUIN_KD", "🧬 Мягкий перенос с манекена", "Плавное распределение весов по ближайшим костям манекена"),
        ],
        default="AUTO",
    )
    aceax_enabled: bpy.props.BoolProperty(
        name="ACE Arsenal Customizable (XtdGear)",
        description="Создать группу вариантов (Бодигруппы / Камуфляжи) для ACE3 Arsenal Extended",
        default=True,
    )
    aceax_model: bpy.props.StringProperty(
        name="Группа (Model ID)",
        description="Общий ID группы в ACE Арсенале (предметы с одинаковым ID объединяются в одну карточку Customizable)",
        default="EZ_Custom_Gear_Pack",
    )
    aceax_label: bpy.props.StringProperty(
        name="Название в ACE",
        description="Название группы Customizable в ACE Арсенале",
        default="EZ Custom Gear",
    )
    aceax_camo: bpy.props.StringProperty(
        name="Скин (Camo)",
        description="Кнопка камуфляжа в ACE Арсенале (например: Multicam, Black, Olive)",
        default="Default",
    )
    aceax_variant: bpy.props.StringProperty(
        name="Вариант (Bodygroup)",
        description="Кнопка варианта/обвеса в ACE Арсенале (например: Base, With_NVG, Clean)",
        default="Base",
    )
    aceax_options: bpy.props.CollectionProperty(type=EZModAceaxOptionItem)
    last_status: bpy.props.StringProperty(
        name="Статус",
        default="",
    )
    show_advanced_rig_tex: bpy.props.BoolProperty(
        name="Доп. настройки (Текстуры / Кость)",
        description="Показать ручной выбор текстур CO/NOHQ и ручной выбор кости",
        default=False,
    )


ARMA3_BONE_LAYOUT = [
    # (bone_name, parent_name, head_xyz, tail_xyz, connected)
    ("pelvis", None, (0.0, 0.01, -0.05), (0.0, 0.0, 0.04), False),
    ("spine", "pelvis", (0.0, 0.0, 0.04), (0.0, 0.005, 0.16), True),
    ("spine1", "spine", (0.0, 0.005, 0.16), (0.0, 0.025, 0.28), True),
    ("spine2", "spine1", (0.0, 0.025, 0.28), (0.0, 0.035, 0.41), True),
    ("spine3", "spine2", (0.0, 0.035, 0.41), (0.0, 0.02, 0.55), True),
    ("neck", "spine3", (0.0, 0.02, 0.55), (0.0, 0.01, 0.61), True),
    ("neck1", "neck", (0.0, 0.01, 0.61), (0.0, -0.01, 0.65), True),
    ("head", "neck1", (0.0, -0.01, 0.65), (0.0, -0.04, 0.82), True),
    # Left Arm
    ("leftshoulder", "spine3", (0.05, 0.03, 0.49), (0.18, 0.05, 0.47), False),
    ("leftarm", "leftshoulder", (0.18, 0.05, 0.47), (0.29, 0.07, 0.37), True),
    ("leftarmroll", "leftarm", (0.29, 0.07, 0.37), (0.39, 0.08, 0.26), True),
    ("leftforearm", "leftarmroll", (0.39, 0.08, 0.26), (0.50, 0.08, 0.15), True),
    ("leftforearmroll", "leftforearm", (0.50, 0.08, 0.15), (0.58, 0.07, 0.08), True),
    ("lefthand", "leftforearmroll", (0.58, 0.07, 0.08), (0.66, 0.05, 0.00), True),
    # Left Fingers (exact b_soldier_01 / Leshiy3 Arma 3 positions)
    ("lefthandthumb1", "lefthand", (0.595, 0.024, 0.052), (0.618, 0.005, 0.026), False),
    ("lefthandthumb2", "lefthandthumb1", (0.618, 0.005, 0.026), (0.638, -0.012, 0.005), True),
    ("lefthandthumb3", "lefthandthumb2", (0.638, -0.012, 0.005), (0.655, -0.028, -0.012), True),
    ("lefthandindex1", "lefthand", (0.660, 0.037, 0.003), (0.683, 0.030, -0.020), False),
    ("lefthandindex2", "lefthandindex1", (0.683, 0.030, -0.020), (0.702, 0.024, -0.041), True),
    ("lefthandindex3", "lefthandindex2", (0.702, 0.024, -0.041), (0.720, 0.018, -0.060), True),
    ("lefthandmiddle1", "lefthand", (0.664, 0.064, -0.002), (0.688, 0.062, -0.029), False),
    ("lefthandmiddle2", "lefthandmiddle1", (0.688, 0.062, -0.029), (0.708, 0.061, -0.051), True),
    ("lefthandmiddle3", "lefthandmiddle2", (0.708, 0.061, -0.051), (0.726, 0.060, -0.071), True),
    ("lefthandring", "lefthand", (0.615, 0.105, 0.028), (0.656, 0.092, -0.002), False),
    ("lefthandring1", "lefthandring", (0.656, 0.092, -0.002), (0.678, 0.095, -0.023), True),
    ("lefthandring2", "lefthandring1", (0.678, 0.095, -0.023), (0.698, 0.099, -0.041), True),
    ("lefthandring3", "lefthandring2", (0.698, 0.099, -0.041), (0.717, 0.103, -0.058), True),
    ("lefthandpinky1", "lefthandring", (0.650, 0.108, 0.002), (0.663, 0.112, -0.013), False),
    ("lefthandpinky2", "lefthandpinky1", (0.663, 0.112, -0.013), (0.679, 0.117, -0.029), True),
    ("lefthandpinky3", "lefthandpinky2", (0.679, 0.117, -0.029), (0.698, 0.122, -0.046), True),
    # Right Arm
    ("rightshoulder", "spine3", (-0.05, 0.03, 0.49), (-0.18, 0.05, 0.47), False),
    ("rightarm", "rightshoulder", (-0.18, 0.05, 0.47), (-0.29, 0.07, 0.37), True),
    ("rightarmroll", "rightarm", (-0.29, 0.07, 0.37), (-0.39, 0.08, 0.26), True),
    ("rightforearm", "rightarmroll", (-0.39, 0.08, 0.26), (-0.50, 0.08, 0.15), True),
    ("rightforearmroll", "rightforearm", (-0.50, 0.08, 0.15), (-0.58, 0.07, 0.08), True),
    ("righthand", "rightforearmroll", (-0.58, 0.07, 0.08), (-0.66, 0.05, 0.00), True),
    # Right Fingers (exact b_soldier_01 / Leshiy3 Arma 3 positions)
    ("righthandthumb1", "righthand", (-0.595, 0.024, 0.052), (-0.618, 0.005, 0.026), False),
    ("righthandthumb2", "righthandthumb1", (-0.618, 0.005, 0.026), (-0.638, -0.012, 0.005), True),
    ("righthandthumb3", "righthandthumb2", (-0.638, -0.012, 0.005), (-0.655, -0.028, -0.012), True),
    ("righthandindex1", "righthand", (-0.660, 0.037, 0.003), (-0.683, 0.030, -0.020), False),
    ("righthandindex2", "righthandindex1", (-0.683, 0.030, -0.020), (-0.702, 0.024, -0.041), True),
    ("righthandindex3", "righthandindex2", (-0.702, 0.024, -0.041), (-0.720, 0.018, -0.060), True),
    ("righthandmiddle1", "righthand", (-0.664, 0.064, -0.002), (-0.688, 0.062, -0.029), False),
    ("righthandmiddle2", "righthandmiddle1", (-0.688, 0.062, -0.029), (-0.708, 0.061, -0.051), True),
    ("righthandmiddle3", "righthandmiddle2", (-0.708, 0.061, -0.051), (-0.726, 0.060, -0.071), True),
    ("righthandring", "righthand", (-0.615, 0.105, 0.028), (-0.656, 0.092, -0.002), False),
    ("righthandring1", "righthandring", (-0.656, 0.092, -0.002), (-0.678, 0.095, -0.023), True),
    ("righthandring2", "righthandring1", (-0.678, 0.095, -0.023), (-0.698, 0.099, -0.041), True),
    ("righthandring3", "righthandring2", (-0.698, 0.099, -0.041), (-0.717, 0.103, -0.058), True),
    ("righthandpinky1", "righthandring", (-0.650, 0.108, 0.002), (-0.663, 0.112, -0.013), False),
    ("righthandpinky2", "righthandpinky1", (-0.663, 0.112, -0.013), (-0.679, 0.117, -0.029), True),
    ("righthandpinky3", "righthandpinky2", (-0.679, 0.117, -0.029), (-0.698, 0.122, -0.046), True),
    # Left Leg
    ("leftupleg", "pelvis", (0.11, 0.00, -0.06), (0.14, 0.01, -0.28), False),
    ("leftuplegroll", "leftupleg", (0.14, 0.01, -0.28), (0.16, 0.02, -0.48), True),
    ("leftleg", "leftuplegroll", (0.16, 0.02, -0.48), (0.17, 0.05, -0.68), True),
    ("leftlegroll", "leftleg", (0.17, 0.05, -0.68), (0.17, 0.06, -0.89), True),
    ("leftfoot", "leftlegroll", (0.17, 0.06, -0.89), (0.17, -0.06, -0.96), True),
    ("lefttoebase", "leftfoot", (0.17, -0.06, -0.96), (0.17, -0.17, -0.98), True),
    # Right Leg
    ("rightupleg", "pelvis", (-0.11, 0.00, -0.06), (-0.14, 0.01, -0.28), False),
    ("rightuplegroll", "rightupleg", (-0.14, 0.01, -0.28), (-0.16, 0.02, -0.48), True),
    ("rightleg", "rightuplegroll", (-0.16, 0.02, -0.48), (-0.17, 0.05, -0.68), True),
    ("rightlegroll", "rightleg", (-0.17, 0.05, -0.68), (-0.17, 0.06, -0.89), True),
    ("rightfoot", "rightlegroll", (-0.17, 0.06, -0.89), (-0.17, -0.06, -0.96), True),
    ("righttoebase", "rightfoot", (-0.17, -0.06, -0.96), (-0.17, -0.17, -0.98), True),
]

ARMA3_BONE_NAMES_SET = {b[0] for b in ARMA3_BONE_LAYOUT}


def ensure_arma3_skeleton(context):
    """Creates or returns the posable Arma3_Skeleton Armature object with all OFP2_ManSkeleton bones."""
    arm_obj = bpy.data.objects.get("Arma3_Skeleton")
    if arm_obj and arm_obj.type == "ARMATURE":
        arm_obj.hide_set(False)
        arm_obj.hide_viewport = False
        missing = [b for b in ARMA3_BONE_LAYOUT if b[0] not in arm_obj.data.bones]
        finger_bones = [b for b in ARMA3_BONE_LAYOUT if "thumb" in b[0] or "index" in b[0] or "middle" in b[0] or "ring" in b[0] or "pinky" in b[0]]
        if missing or finger_bones:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            prev_active = context.view_layer.objects.active
            context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode="EDIT")
            ebones = arm_obj.data.edit_bones
            for bname, pname, head_pt, tail_pt, is_conn in missing:
                if bname in ebones:
                    continue
                eb = ebones.new(bname)
                eb.head = head_pt
                eb.tail = tail_pt
                if pname and pname in ebones:
                    eb.parent = ebones[pname]
                    eb.use_connect = is_conn
            for bname, pname, head_pt, tail_pt, is_conn in finger_bones:
                if bname in ebones:
                    eb = ebones[bname]
                    eb.head = head_pt
                    eb.tail = tail_pt
            bpy.ops.object.mode_set(mode="OBJECT")
            if prev_active and prev_active.name in bpy.data.objects:
                context.view_layer.objects.active = prev_active
        return arm_obj

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    arm_data = bpy.data.armatures.new("Arma3_Skeleton_Data")
    arm_obj = bpy.data.objects.new("Arma3_Skeleton", arm_data)
    context.scene.collection.objects.link(arm_obj)

    arm_obj.show_in_front = True
    arm_data.display_type = "OCTAHEDRAL"

    prev_active = context.view_layer.objects.active
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    ebones = arm_data.edit_bones
    created = {}
    for bname, pname, head_pt, tail_pt, is_conn in ARMA3_BONE_LAYOUT:
        eb = ebones.new(bname)
        eb.head = head_pt
        eb.tail = tail_pt
        if pname and pname in created:
            eb.parent = created[pname]
            eb.use_connect = is_conn
        created[bname] = eb

    bpy.ops.object.mode_set(mode="OBJECT")
    if prev_active and prev_active.name in bpy.data.objects:
        context.view_layer.objects.active = prev_active

    return arm_obj


def _ensure_mannequin_finger_weights(mannequin_obj):
    """Populates all 32 finger bone weights onto Arma3_Mannequin from b_soldier_01 MLOD if missing."""
    if not mannequin_obj or mannequin_obj.type != "MESH":
        return
    if mannequin_obj.vertex_groups.get("lefthandindex1") is not None:
        return
    if not os.path.exists(FINGER_REF_PATH):
        return

    ref_verts, _, _, ref_sels = load_mlod_lod0_data(FINGER_REF_PATH)
    hand_bones = [
        bname for bname in ref_sels.keys()
        if ("hand" in bname.lower() or "forearmroll" in bname.lower())
        and bname.lower() in ARMA3_BONE_NAMES_SET
    ]
    if not hand_bones:
        return

    ref_indices = set()
    for bname in hand_bones:
        ref_indices.update(ref_sels[bname].keys())
    if not ref_indices:
        return

    ref_list = sorted(ref_indices)
    kd = kdtree.KDTree(len(ref_list))
    for r_idx in ref_list:
        kd.insert(Vector(ref_verts[r_idx]), r_idx)
    kd.balance()

    vgroups = {
        bname: (mannequin_obj.vertex_groups.get(bname) or mannequin_obj.vertex_groups.new(name=bname))
        for bname in hand_bones
    }
    vg_lh = mannequin_obj.vertex_groups.get("lefthand")
    vg_rh = mannequin_obj.vertex_groups.get("righthand")

    for vi, v in enumerate(mannequin_obj.data.vertices):
        if abs(v.co.x) < 0.54:
            continue
        nearest = kd.find_n(v.co, 3)
        total_inv = 0.0
        acc = {}
        for _, r_idx, dist in nearest:
            inv = 1.0 / (max(dist, 0.0005) ** 2)
            total_inv += inv
            for bname in hand_bones:
                w = ref_sels[bname].get(r_idx, 0.0)
                if w > 0.0:
                    acc[bname] = acc.get(bname, 0.0) + w * inv
        if total_inv > 0.0 and acc:
            if vg_lh and v.co.x > 0:
                vg_lh.remove([vi])
            if vg_rh and v.co.x < 0:
                vg_rh.remove([vi])
            for bname, wsum in acc.items():
                w = wsum / total_inv
                if w > 0.02:
                    vgroups[bname].add([vi], min(1.0, w), "REPLACE")


def bind_mesh_to_arma3_skeleton(mesh_obj, arm_obj):
    """Attaches an Armature modifier to mesh_obj pointing to arm_obj so posing deforms the mesh."""
    if not mesh_obj or not arm_obj:
        return
    mod = mesh_obj.modifiers.get("Arma3_Rig")
    if not mod:
        for m in mesh_obj.modifiers:
            if m.type == "ARMATURE":
                mod = m
                break
    if not mod:
        mod = mesh_obj.modifiers.new(name="Arma3_Rig", type="ARMATURE")
    mod.object = arm_obj
    mod.use_vertex_groups = True


class EZMOD_OT_spawn_mannequin(bpy.types.Operator):
    bl_idname = "ezmod.spawn_mannequin"
    bl_label = "👤 Спавн манекена + Скелет Arma 3"
    bl_description = "Загружает бойца Arma 3 (без прокси-треугольников) вместе с подвижным скелетом Arma3_Skeleton для проверки весов и растяжения"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not os.path.exists(MANNEQUIN_PATH):
            self.report({"ERROR"}, f"Файл манекена не найден: {MANNEQUIN_PATH}")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # Remove old mannequin if it had old mesh or lacked skeleton binding
        existing = bpy.data.objects.get("Arma3_Mannequin")
        if existing:
            bpy.data.objects.remove(existing, do_unlink=True)

        arm_obj = ensure_arma3_skeleton(context)
        verts_bl, faces, face_uvs, selections = load_mlod_lod0_data(MANNEQUIN_PATH)

        mesh = bpy.data.meshes.new("Arma3_Mannequin_Mesh")
        mesh.from_pydata(verts_bl, [], faces)
        mesh.update()

        for poly in mesh.polygons:
            poly.use_smooth = True

        obj = bpy.data.objects.new("Arma3_Mannequin", mesh)
        context.scene.collection.objects.link(obj)
        obj.parent = arm_obj

        # Add skeletal vertex groups
        for bname, wmap in selections.items():
            vg = obj.vertex_groups.new(name=bname)
            for vi, w in wmap.items():
                vg.add([vi], w, "REPLACE")

        _ensure_mannequin_finger_weights(obj)
        bind_mesh_to_arma3_skeleton(obj, arm_obj)

        # Studio Mannequin Material
        mat = bpy.data.materials.get("EZ_Mannequin_Mat")
        if not mat:
            mat = bpy.data.materials.new(name="EZ_Mannequin_Mat")
            if not getattr(mat, "use_nodes", True):
                mat.use_nodes = True
            if mat.node_tree:
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf:
                    bsdf.inputs["Base Color"].default_value = (0.18, 0.24, 0.32, 1.0)
                    bsdf.inputs["Roughness"].default_value = 0.65
            mat.diffuse_color = (0.18, 0.24, 0.32, 1.0)
        obj.data.materials.append(mat)

        self.report({"INFO"}, "Манекен + подвижный скелет Arma3_Skeleton загружены! Можно гнуть кости и править веса.")
        return {"FINISHED"}


class EZMOD_OT_toggle_mannequin(bpy.types.Operator):
    bl_idname = "ezmod.toggle_mannequin"
    bl_label = "👁 Скрыть / Показать манекен"
    bl_description = "Переключает видимость манекена Arma3_Mannequin и скелета в окне 3D"

    def execute(self, context):
        obj = bpy.data.objects.get("Arma3_Mannequin")
        arm = bpy.data.objects.get("Arma3_Skeleton")
        if not obj and not arm:
            self.report({"WARNING"}, "Манекен ещё не загружен. Нажмите 'Спавн манекена'!")
            return {"CANCELLED"}
        state = not (obj.hide_get() if obj else arm.hide_get())
        if obj:
            obj.hide_set(state)
        if arm:
            arm.hide_set(state)
        return {"FINISHED"}


class EZMOD_OT_test_pose(bpy.types.Operator):
    bl_idname = "ezmod.test_pose"
    bl_label = "🤸 Тест позы (Проверка растяжения)"
    bl_description = "Сгибает спину, плечи, руки и поворачивает голову скелета Arma3_Skeleton, чтобы вы сразу увидели, как тянется снаряга"
    bl_options = {"REGISTER", "UNDO"}

    pose_step: bpy.props.IntProperty(default=0)

    def execute(self, context):
        from mathutils import Euler
        import math

        arm = bpy.data.objects.get("Arma3_Skeleton")
        if not arm or arm.type != "ARMATURE":
            self.report({"WARNING"}, "Сначала нажмите 'Спавн манекена + Скелет Arma 3'!")
            return {"CANCELLED"}

        # Cycle through 3 dynamic test poses using a custom property on scene
        cur = int(context.scene.get("ez_pose_idx", 0))
        next_pose = (cur + 1) % 3
        context.scene["ez_pose_idx"] = next_pose

        for pb in arm.pose.bones:
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.location = (0.0, 0.0, 0.0)

        def rot(bname, rx, ry, rz):
            pb = arm.pose.bones.get(bname)
            if pb:
                pb.rotation_mode = "XYZ"
                pb.rotation_euler = Euler((math.radians(rx), math.radians(ry), math.radians(rz)), "XYZ")

        if next_pose == 1:
            # Pose 1: Combat torso twist + raised arms + head look
            rot("spine1", 8, 0, -12)
            rot("spine2", 10, 5, -15)
            rot("spine3", 12, 5, -15)
            rot("neck", -8, 0, 20)
            rot("head", -10, 8, 25)
            rot("leftshoulder", 0, -18, 12)
            rot("leftarm", 35, -35, 25)
            rot("leftforearm", 45, 0, -30)
            rot("rightshoulder", 0, 18, -10)
            rot("rightarm", 25, 45, -20)
            rot("rightforearm", 55, 0, 35)
            self.report({"INFO"}, "Тест-поза 1: Скрутка корпуса + боевая стойка рук (нажми ещё раз для Позы 2)")
        elif next_pose == 2:
            # Pose 2: Deep lean forward + arms wide up (extreme shoulder/vest stretch check)
            rot("spine1", -15, 0, 0)
            rot("spine2", -18, 0, 18)
            rot("spine3", -15, 0, 20)
            rot("head", 25, 0, -25)
            rot("leftshoulder", 0, -25, 0)
            rot("leftarm", 0, -55, 0)
            rot("rightshoulder", 0, 25, 0)
            rot("rightarm", 0, 55, 0)
            self.report({"INFO"}, "Тест-поза 2: Поднятие рук и наклон спины (проверка плеч и лямок)")
        else:
            self.report({"INFO"}, "Поза сброшена в исходную Т-позу!")

        return {"FINISHED"}


class EZMOD_OT_reset_pose(bpy.types.Operator):
    bl_idname = "ezmod.reset_pose"
    bl_label = "🔄 Т-Поза (Сброс)"
    bl_description = "Возвращает скелет Arma3_Skeleton в исходную ровную Т-позу"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = bpy.data.objects.get("Arma3_Skeleton")
        if not arm or arm.type != "ARMATURE":
            return {"CANCELLED"}
        context.scene["ez_pose_idx"] = 0
        for pb in arm.pose.bones:
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.location = (0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
        self.report({"INFO"}, "Скелет возвращён в ровную Т-позу!")
        return {"FINISHED"}


class EZMOD_OT_weight_paint_mode(bpy.types.Operator):
    bl_idname = "ezmod.weight_paint_mode"
    bl_label = "🖌 Править веса кистью (Weight Paint)"
    bl_description = "Переключает выбранную модель снаряжения в режим Weight Paint вместе со скелетом Arma3_Skeleton (Ctrl+ЛКМ по кости, чтобы выбрать кость и красить веса)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != "MESH" or obj.name == "Arma3_Mannequin" or obj.name.startswith("Ref_"):
            self.report({"ERROR"}, "Сначала выделите вашу модель снаряжения (броник/рюкзак/шлем)!")
            return {"CANCELLED"}

        if context.mode == "PAINT_WEIGHT":
            bpy.ops.object.mode_set(mode="OBJECT")
            self.report({"INFO"}, "Вышли из режима Weight Paint в Object Mode.")
            return {"FINISHED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        arm = ensure_arma3_skeleton(context)
        bind_mesh_to_arma3_skeleton(obj, arm)

        # Ensure vertex groups exist before entering Weight Paint
        has_arma_vg = any(vg.name in ARMA3_BONE_NAMES_SET for vg in obj.vertex_groups)
        if not has_arma_vg:
            bpy.ops.ezmod.auto_rig()

        bpy.ops.object.select_all(action="DESELECT")
        arm.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="WEIGHT_GPENCIL" if False else "WEIGHT_PAINT")

        # Select spine3 or head active group for convenience
        props = context.scene.ez_mod_props
        pref_bone = "head" if props.gear_type in ("HEADGEAR", "GOGGLES", "FACE") else "spine3"
        vg = obj.vertex_groups.get(pref_bone)
        if vg:
            obj.vertex_groups.active_index = vg.index

        self.report(
            {"INFO"},
            "Режим Weight Paint включён! Меняй кости во вкладке Vertex Groups (или Ctrl+ЛКМ по кости) и правь веса кистью.",
        )
        return {"FINISHED"}


class EZMOD_OT_spawn_ref_gear(bpy.types.Operator):
    bl_idname = "ezmod.spawn_ref_gear"
    bl_label = "🎯 Спавн эталона (Посадка/Масштаб)"
    bl_description = "Подгружает полупрозрачный образец реальной снаряги (шлем/броник/рюкзак/повязка) для проверки габаритов"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ez_mod_props
        gtype = props.gear_type
        ref_key = "HEADGEAR" if gtype in ("HEADGEAR", "GOGGLES", "FACE") else gtype
        ref_path, ref_name = REF_MODELS.get(ref_key, REF_MODELS["HEADGEAR"])

        existing = bpy.data.objects.get(ref_name)
        if existing:
            bpy.data.objects.remove(existing, do_unlink=True)
            self.report({"INFO"}, f"Эталон {ref_name} убран со сцены")
            return {"FINISHED"}

        if not os.path.exists(ref_path):
            self.report({"WARNING"}, f"Эталон не найден: {ref_path}")
            return {"CANCELLED"}

        verts_bl, faces, _, _ = load_mlod_lod0_data(ref_path)
        mesh = bpy.data.meshes.new(ref_name + "_Mesh")
        mesh.from_pydata(verts_bl, [], faces)
        mesh.update()

        obj = bpy.data.objects.new(ref_name, mesh)
        obj.display_type = "WIRE"
        obj.color = (0.0, 0.95, 1.0, 0.5)
        context.scene.collection.objects.link(obj)
        self.report({"INFO"}, f"Эталон {ref_name} добавлен (нажмите ещё раз, чтобы убрать)")
        return {"FINISHED"}


def import_manifest_into_blender_scene(context, manifest):
    """
    Imports a prepared Arma 3 model manifest into the active Blender scene:
    1. Builds the 3D mesh with exact UVs, smooth shading, and material indices
    2. Creates Principled BSDF materials with converted Diffuse (_co) and Normal (_nohq) PNG textures
    3. Restores all Arma 3 Vertex Groups (bone weights + camo selections) and binds to Arma3_Skeleton
    4. Populates all EZ_MOD settings in the panel (Слот, Имя, ID, Автор, Броня, Вмест., Масса, 1-е лицо, ACE варианты)
    """
    import importlib
    importlib.reload(ez_mod_core)

    mlod_path = manifest.get("mlod_p3d_path", "")
    if not mlod_path or not os.path.exists(mlod_path):
        raise RuntimeError(f"Не найден файл MLOD P3D: {mlod_path}")

    if context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    parsed = ez_mod_core.parse_full_mlod_for_blender(mlod_path)
    verts_bl = parsed["verts_bl"]
    faces = parsed["faces"]
    face_uvs = parsed["face_uvs"]
    face_mat_indices = parsed["face_mat_indices"]
    selections = parsed["selections"]

    model_stem = manifest.get("model_stem") or os.path.splitext(os.path.basename(mlod_path))[0].replace("_mlod", "")
    obj_name = manifest.get("class_slug") or model_stem

    # Ensure Mannequin & Skeleton exist in scene so imported model sits directly on the character
    arm = ensure_arma3_skeleton(context)
    if bpy.data.objects.get("Arma3_Mannequin") is None:
        try:
            bpy.ops.ezmod.spawn_mannequin()
            arm = ensure_arma3_skeleton(context)
        except Exception:
            pass

    mesh = bpy.data.meshes.new(f"{obj_name}_Mesh")
    mesh.from_pydata(verts_bl, [], faces)
    mesh.update()

    for poly in mesh.polygons:
        poly.use_smooth = True

    # UVs
    uv_layer = mesh.uv_layers.new(name="UVMap")
    mesh.uv_layers.active = uv_layer
    for fi, poly in enumerate(mesh.polygons):
        if fi < len(face_uvs):
            f_uv = face_uvs[fi]
            for li, loop_idx in enumerate(poly.loop_indices):
                if li < len(f_uv):
                    uv_layer.data[loop_idx].uv = f_uv[li]

    # Materials & Textures
    mat_slots_meta = manifest.get("material_slots") or parsed.get("material_slots") or []
    for idx, smeta in enumerate(mat_slots_meta):
        sname = smeta.get("slot_name") or f"camo{idx if idx > 0 else ''}"
        mat_name = f"M_{model_stem}_{sname}"
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bsdf = nt.nodes.new(type="ShaderNodeBsdfPrincipled")

        diff_png = smeta.get("diffuse_png", "")
        if diff_png and os.path.exists(diff_png):
            tex_node = nt.nodes.new(type="ShaderNodeTexImage")
            tex_node.name = "Diffuse Texture"
            tex_node.label = f"{sname}_co"
            tex_node.location = (-420, 260)
            tex_node.image = bpy.data.images.load(diff_png, check_existing=True)
            if "Base Color" in bsdf.inputs:
                nt.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

        norm_png = smeta.get("normal_png", "")
        if norm_png and os.path.exists(norm_png):
            ntex_node = nt.nodes.new(type="ShaderNodeTexImage")
            ntex_node.name = "Normal Map Texture"
            ntex_node.label = f"{sname}_nohq"
            ntex_node.location = (-680, -140)
            nimg = bpy.data.images.load(norm_png, check_existing=True)
            try:
                nimg.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            ntex_node.image = nimg

            nmap_node = nt.nodes.new(type="ShaderNodeNormalMap")
            nmap_node.location = (-340, -140)
            nmap_node.space = "TANGENT"
            nt.links.new(ntex_node.outputs["Color"], nmap_node.inputs["Color"])
            if "Normal" in bsdf.inputs:
                nt.links.new(nmap_node.outputs["Normal"], bsdf.inputs["Normal"])

        mesh.materials.append(mat)

    if len(mesh.materials) > 0:
        max_m = len(mesh.materials) - 1
        for fi, poly in enumerate(mesh.polygons):
            if fi < len(face_mat_indices):
                poly.material_index = min(max_m, max(0, int(face_mat_indices[fi])))

    obj = bpy.data.objects.new(obj_name, mesh)
    context.scene.collection.objects.link(obj)

    # Restore Vertex Groups (Bone Weights + Camo Selections)
    has_bone_weights = False
    for sel_name, weights_dict in selections.items():
        if not weights_dict:
            continue
        vg_name = sel_name.lower() if sel_name.lower() in ARMA3_BONE_NAMES_SET else sel_name
        if vg_name in ARMA3_BONE_NAMES_SET:
            has_bone_weights = True
        vg = obj.vertex_groups.get(vg_name) or obj.vertex_groups.new(name=vg_name)
        for vi, wval in weights_dict.items():
            vg.add([int(vi)], float(wval), "REPLACE")

    # Apply EZ_MOD Settings to Scene Properties
    props = context.scene.ez_mod_props
    valid_gtypes = {"HEADGEAR", "VEST", "UNIFORM", "BACKPACK", "GOGGLES", "FACE", "NVG"}
    raw_gt = manifest.get("gear_type", "HEADGEAR")
    props.gear_type = raw_gt if raw_gt in valid_gtypes else "HEADGEAR"
    props.item_name = str(manifest.get("item_name") or obj_name)
    props.class_slug = str(manifest.get("class_slug") or obj_name)
    props.author_name = str(manifest.get("author_name") or "EZ_MOD Creator")
    props.armor_val = max(0, min(200, int(manifest.get("armor_val", 10))))
    props.mass_val = max(1, min(500, int(manifest.get("mass_val", 20))))
    props.load_val = max(0, min(2000, int(manifest.get("load_val", 160))))
    props.hide_in_first_person = bool(manifest.get("hide_in_first_person", False))
    props.diffuse_tex = ""
    props.normal_tex = ""
    props.aceax_model = props.class_slug
    props.aceax_label = props.item_name

    # If model had no bone weights in P3D, compute automatic weights for its slot
    if not has_bone_weights:
        auto_groups = compute_auto_weights_for_points(verts_bl, props.gear_type, "AUTO")
        for bname, bweights in auto_groups.items():
            vg = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)
            for vi, wval in bweights.items():
                vg.add([int(vi)], float(wval), "REPLACE")

    if arm:
        bind_mesh_to_arma3_skeleton(obj, arm)

    # Select & activate imported object
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    # Switch 3D Viewport to Material Preview so textures are visible immediately
    try:
        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D" and space.shading.type == "SOLID":
                        space.shading.type = "MATERIAL"
    except Exception:
        pass

    props.last_status = f"📥 Импортировано: {props.item_name} ({len(faces)} пол., {len(mesh.materials)} мат.)"
    return obj


class EZMOD_OT_import_selected_model(bpy.types.Operator):
    bl_idname = "ezmod.import_selected_model"
    bl_label = "📥 Импорт выбранной модели (со всеми настройками)"
    bl_description = (
        "Загружает выбранную в Инспекторе (ArmaModManager) 3D-модель прямо в Blender: "
        "со всеми материалами, текстурами (CO + NOHQ), весами костей и авто-заполнением настроек EZ_MOD"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ez_mod_props
        chosen_mlod = getattr(props, "bridge_selected_model", "")
        hist_path = os.path.join(r"Z:\aarma\extracted\blender_bridge", "history.json")
        latest_path = os.path.join(r"Z:\aarma\extracted\blender_bridge", "latest_import.json")

        manifest = None
        if chosen_mlod and chosen_mlod != "NONE" and os.path.exists(hist_path):
            try:
                with open(hist_path, "r", encoding="utf-8") as f:
                    for item in json.load(f):
                        if item.get("mlod_p3d_path") == chosen_mlod:
                            manifest = item
                            break
            except Exception:
                pass

        if manifest is None and os.path.exists(latest_path):
            try:
                with open(latest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass

        if not manifest:
            self.report(
                {"WARNING"},
                "В очереди нет модели! Нажми '📥 В Blender' в окне Инспектора модов или используй кнопку '📂 Из файла .p3d'",
            )
            return {"CANCELLED"}

        try:
            obj = import_manifest_into_blender_scene(context, manifest)
        except Exception as e:
            self.report({"ERROR"}, f"Ошибка импорта модели: {e}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"✅ Модель '{manifest.get('item_name')}' импортирована со всеми текстурами, весами и настройками ({obj.name})!",
        )
        return {"FINISHED"}


class EZMOD_OT_import_p3d_file(bpy.types.Operator):
    bl_idname = "ezmod.import_p3d_file"
    bl_label = "📂 Выбрать .p3d с диска (Авто-настройки)"
    bl_description = (
        "Открывает любой файл .p3d (даже забинаренный ODOL!), автоматически дебинаризует его, "
        "подтягивает текстуры и config.bin из папки/PBO и загружает в Blender со всеми настройками"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.p3d", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({"ERROR"}, "Файл .p3d не найден!")
            return {"CANCELLED"}

        try:
            import importlib
            importlib.reload(ez_mod_core)
            manifest = ez_mod_core.prepare_model_for_blender_import(pbo_path="", model_file=self.filepath)
            obj = import_manifest_into_blender_scene(context, manifest)
        except Exception as e:
            self.report({"ERROR"}, f"Ошибка импорта .p3d: {e}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"✅ Файл '{os.path.basename(self.filepath)}' дебинаризован и импортирован со всеми настройками ({obj.name})!",
        )
        return {"FINISHED"}


def _resolve_target_gear_mesh(context):
    """Returns active gear mesh, or finds the first visible gear mesh if skeleton/helper is active."""
    obj = context.active_object
    if obj and obj.type == "MESH" and not _is_helper_scene_obj(obj):
        return obj
    for cand in context.selected_objects:
        if cand.type == "MESH" and not _is_helper_scene_obj(cand):
            return cand
    for cand in context.scene.objects:
        if cand.type == "MESH" and not _is_helper_scene_obj(cand) and not cand.hide_get():
            return cand
    return None


class EZMOD_OT_auto_fit_gloves(bpy.types.Operator):
    bl_idname = "ezmod.auto_fit_gloves"
    bl_label = "🧤 Авто-подгонка перчаток на руки (L/R + Веса пальцев)"
    bl_description = (
        "Автоматически разворачивает и сажает перчатку (или обе перчатки) точно на кисть манекена Arma 3 "
        "(методом совмещения осей PCA/OBB), отзеркаливает на вторую руку и переносит веса всех 30 костей пальцев"
    )
    bl_options = {"REGISTER", "UNDO"}

    mirror_both_hands: bpy.props.BoolProperty(
        name="Сделать сразу на ОБЕ руки (L + R)",
        description="Если у вас только одна перчатка — автоматически создаст отзеркаленную копию на вторую руку и сошьёт их",
        default=True,
    )
    scale_inflate: bpy.props.FloatProperty(
        name="Запас объёма (чтобы не торчала кожа)",
        default=1.06,
        min=0.85,
        max=1.35,
    )

    def execute(self, context):
        import numpy as np
        from mathutils import Matrix, Vector

        obj = _resolve_target_gear_mesh(context)
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Выделите 3D-модель перчатки!")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        mannequin = bpy.data.objects.get("Arma3_Mannequin")
        if mannequin is None:
            bpy.ops.ezmod.spawn_mannequin()
            mannequin = bpy.data.objects.get("Arma3_Mannequin")
        if mannequin is None:
            self.report({"ERROR"}, "Не удалось загрузить манекен Arma3_Mannequin!")
            return {"CANCELLED"}

        arm = ensure_arma3_skeleton(context)

        # Reset pose temporarily so hand coordinates match rest pose
        for pb in arm.pose.bones:
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.location = (0.0, 0.0, 0.0)
        context.view_layer.update()

        # Collect Arma 3 left hand vertices (x > 0.54) from mannequin
        lh_pts = []
        for v in mannequin.data.vertices:
            co = mannequin.matrix_world @ v.co
            if co.x > 0.54:
                lh_pts.append((co.x, co.y, co.z))
        if len(lh_pts) < 20:
            self.report({"ERROR"}, "Не найдены вершины кисти на манекене!")
            return {"CANCELLED"}

        tgt_arr = np.array(lh_pts, dtype=np.float64)
        tgt_center = tgt_arr.mean(axis=0)
        tgt_centered = tgt_arr - tgt_center
        # PCA on target left hand
        _, tgt_s, tgt_vt = np.linalg.svd(tgt_centered, full_matrices=False)
        tgt_axes = tgt_vt  # 3x3 rows = principal axes (0: longest finger-to-wrist, 1: palm width, 2: palm thickness)
        tgt_span = np.percentile(tgt_centered @ tgt_axes.T, 97, axis=0) - np.percentile(tgt_centered @ tgt_axes.T, 3, axis=0)

        # Ensure target axis 0 points from wrist toward fingertips (+X, -Z in Arma 3 rest pose)
        wrist_to_tip = np.array([0.68 - 0.56, 0.05 - 0.07, -0.04 - 0.08], dtype=np.float64)
        if np.dot(tgt_axes[0], wrist_to_tip) < 0:
            tgt_axes[0] = -tgt_axes[0]
        # Ensure target axis 1 points toward thumb (-Y in Arma 3 left hand: thumb is at y=0.005, pinky at y=0.112)
        palm_to_thumb = np.array([0.0, -1.0, 0.0], dtype=np.float64)
        if np.dot(tgt_axes[1], palm_to_thumb) < 0:
            tgt_axes[1] = -tgt_axes[1]
        # Right-handed basis
        tgt_axes[2] = np.cross(tgt_axes[0], tgt_axes[1])
        tgt_axes[2] /= max(np.linalg.norm(tgt_axes[2]), 1e-8)

        # Collect source glove vertices in world space
        mw = obj.matrix_world
        src_pts = [tuple(mw @ v.co) for v in obj.data.vertices]
        if len(src_pts) < 10:
            self.report({"ERROR"}, "В модели перчатки слишком мало вершин!")
            return {"CANCELLED"}

        src_arr = np.array(src_pts, dtype=np.float64)
        # If the mesh already has two distant gloves (L & R), only take one side if mirror_both_hands is True
        x_span = src_arr[:, 0].max() - src_arr[:, 0].min()
        has_two_hands = x_span > 0.45

        if not has_two_hands:
            src_center = src_arr.mean(axis=0)
            src_centered = src_arr - src_center
            _, _, src_vt = np.linalg.svd(src_centered, full_matrices=False)
            src_axes = src_vt.copy()
            proj0 = src_centered @ src_axes[0]
            # Fingertips end has narrower perimeter/spread or more extreme tip than cuff, or check vertex density:
            # In gloves, fingertips have more vertices (5 finger tubes) than the wrist cuff!
            q_pos = proj0 > np.percentile(proj0, 65)
            q_neg = proj0 < np.percentile(proj0, 35)
            if q_pos.sum() < q_neg.sum():
                src_axes[0] = -src_axes[0]
                proj0 = -proj0

            # Thumb side: along axis 1, thumb sticks out mid-way (around proj0 in [-0.2..0.2])
            proj1 = src_centered @ src_axes[1]
            mid_mask = (proj0 > np.percentile(proj0, 20)) & (proj0 < np.percentile(proj0, 65))
            if mid_mask.any():
                p1_mid = proj1[mid_mask]
                if abs(np.percentile(p1_mid, 98)) > abs(np.percentile(p1_mid, 2)):
                    # Positive proj1 is thumb side
                    pass
                else:
                    src_axes[1] = -src_axes[1]
            src_axes[2] = np.cross(src_axes[0], src_axes[1])
            src_axes[2] /= max(np.linalg.norm(src_axes[2]), 1e-8)

            src_proj = src_centered @ src_axes.T
            src_span = np.percentile(src_proj, 97, axis=0) - np.percentile(src_proj, 3, axis=0)
            scale_factor = float(tgt_span[0] / max(src_span[0], 1e-5)) * float(self.scale_inflate)

            # Transform source points into target left hand frame
            fitted_lh = (src_proj * scale_factor) @ tgt_axes + tgt_center

            mw_inv = obj.matrix_world.inverted_safe()
            for vi, v in enumerate(obj.data.vertices):
                w_co = Vector((float(fitted_lh[vi, 0]), float(fitted_lh[vi, 1]), float(fitted_lh[vi, 2])))
                v.co = mw_inv @ w_co
            obj.data.update()

            if self.mirror_both_hands:
                # Duplicate and mirror across X=0 so both Left and Right hands get gloves
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                # Transform to world space inside bmesh
                bmesh.ops.transform(bm, matrix=obj.matrix_world, verts=bm.verts)
                geom_all = list(bm.verts) + list(bm.edges) + list(bm.faces)
                ret = bmesh.ops.dupe(bm, geom=geom_all)
                dup_verts = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]
                dup_faces = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMFace)]
                for dv in dup_verts:
                    dv.co.x = -dv.co.x
                bmesh.ops.reverse_faces(bm, faces=dup_faces)
                bmesh.ops.transform(bm, matrix=mw_inv, verts=bm.verts)
                bm.normal_update()
                bm.to_mesh(obj.data)
                bm.free()
                obj.data.update()

        # Transfer all 30 finger + hand bone weights from Arma3_Mannequin
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        for vg in list(obj.vertex_groups):
            if vg.name.lower() in ARMA3_BONE_NAMES_SET:
                obj.vertex_groups.remove(vg)

        dt_mod = obj.modifiers.new(name="Glove_WeightTransfer", type="DATA_TRANSFER")
        dt_mod.object = mannequin
        dt_mod.use_vert_data = True
        dt_mod.data_types_verts = {"VGROUP_WEIGHTS"}
        dt_mod.vert_mapping = "POLYINTERP_NEAREST"
        dt_mod.layers_vgroup_select_src = "ALL"
        dt_mod.layers_vgroup_select_dst = "NAME"
        bpy.ops.object.datalayout_transfer(modifier=dt_mod.name)
        bpy.ops.object.modifier_apply(modifier=dt_mod.name)

        vg_camo = obj.vertex_groups.get("camo") or obj.vertex_groups.new(name="camo")
        vg_camo.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

        bind_mesh_to_arma3_skeleton(obj, arm)
        try:
            if len(obj.vertex_groups) > 0:
                obj.vertex_groups.active_index = 0
            bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
            bpy.ops.object.vertex_group_limit_total(group_select_mode="BONE_DEFORM", limit=4)
            bpy.ops.object.vertex_group_normalize_all(group_select_mode="BONE_DEFORM", lock_active=False)
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

        self.report(
            {"INFO"},
            "🧤 Перчатки посажены на кисти манекена, отзеркалены (L+R) и привязаны ко всем 30 костям пальцев!",
        )
        return {"FINISHED"}


class EZMOD_OT_auto_rig(bpy.types.Operator):
    bl_idname = "ezmod.auto_rig"
    bl_label = "🧲 Авто-привязка к скелету (Auto-Rig)"
    bl_description = "Автоматически рассчитывает веса костей Arma 3 (head / spine3 / pelvis / плечи) и привязывает вашу модель к живому скелету Arma3_Skeleton для проверки растяжения и Weight Paint"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Не найдена 3D-модель снаряжения (Mesh)!")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        props = context.scene.ez_mod_props
        arm = ensure_arma3_skeleton(context)

        # Save & reset skeleton pose temporarily so rest-pose weight transfer is accurate
        saved_pose = {}
        if arm and arm.pose:
            for pb in arm.pose.bones:
                saved_pose[pb.name] = (
                    pb.rotation_mode,
                    tuple(pb.rotation_euler),
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                )
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.location = (0.0, 0.0, 0.0)

        # Clear old Arma bone weights so re-running Auto-Rig gives a fresh clean rig
        for vg in list(obj.vertex_groups):
            if vg.name.lower() in ARMA3_BONE_NAMES_SET or vg.name.lower().startswith("camo"):
                obj.vertex_groups.remove(vg)

        used_rwt = False
        rwt_error = None
        use_surface_transfer = (
            props.rig_bone_mode in ("AUTO", "MANNEQUIN_KD")
            and props.gear_type not in ("HEADGEAR", "GOGGLES", "FACE")
            and hasattr(context.scene, "robust_weight_transfer_settings")
            and hasattr(bpy.ops.object, "skin_weight_transfer")
        )

        if use_surface_transfer:
            mannequin = bpy.data.objects.get("Arma3_Mannequin")
            if mannequin is None:
                try:
                    bpy.ops.ezmod.spawn_mannequin()
                    mannequin = bpy.data.objects.get("Arma3_Mannequin")
                except Exception:
                    mannequin = None

            if mannequin is not None and mannequin.type == "MESH":
                man_was_hidden = mannequin.hide_get()
                mannequin.hide_set(False)
                bind_mesh_to_arma3_skeleton(mannequin, arm)
                try:
                    bpy.ops.object.select_all(action="DESELECT")
                    obj.select_set(True)
                    context.view_layer.objects.active = obj
                    rwt = context.scene.robust_weight_transfer_settings
                    rwt.source_object = mannequin
                    rwt.apply_to_selected = False
                    if hasattr(rwt, "group_selection"):
                        rwt.group_selection = "DEFORM_POSE_BONES"
                    if hasattr(rwt, "max_distance"):
                        rwt.max_distance = max(rwt.max_distance, 0.25)
                    if hasattr(rwt, "max_normal_angle_difference"):
                        import math
                        rwt.max_normal_angle_difference = math.pi
                    context.view_layer.update()
                    if bpy.ops.object.skin_weight_transfer.poll():
                        res = bpy.ops.object.skin_weight_transfer()
                        if "FINISHED" in res:
                            used_rwt = True
                except Exception as exc:
                    rwt_error = str(exc)
                finally:
                    mannequin.hide_set(man_was_hidden)

        if used_rwt:
            bind_mesh_to_arma3_skeleton(obj, arm)
            # Smooth + limit to 4 bones per vertex (Arma 3 limit) before adding 'camo'
            try:
                if len(obj.vertex_groups) > 0:
                    obj.vertex_groups.active_index = 0
                bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                bpy.ops.object.vertex_group_smooth(
                    group_select_mode="BONE_DEFORM",
                    factor=0.35,
                    repeat=2,
                    expand=0.0,
                )
                bpy.ops.object.vertex_group_limit_total(
                    group_select_mode="BONE_DEFORM",
                    limit=4,
                )
                bpy.ops.object.vertex_group_normalize_all(
                    group_select_mode="BONE_DEFORM",
                    lock_active=False,
                )
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                if context.mode != "OBJECT":
                    try:
                        bpy.ops.object.mode_set(mode="OBJECT")
                    except Exception:
                        pass
            vg_camo = obj.vertex_groups.get("camo") or obj.vertex_groups.new(name="camo")
            vg_camo.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
            bones_list = ", ".join(
                vg.name for vg in obj.vertex_groups if vg.name.lower() in ARMA3_BONE_NAMES_SET
            )
        else:
            # Temporarily disable armature modifier while sampling rest-pose world positions
            disabled_mods = []
            for m in obj.modifiers:
                if m.type == "ARMATURE" and m.show_viewport:
                    m.show_viewport = False
                    disabled_mods.append(m)
            context.view_layer.update()

            mw = obj.matrix_world
            pts_bl = [tuple(mw @ v.co) for v in obj.data.vertices]
            weights_map = compute_auto_weights_for_points(pts_bl, props.gear_type, bone_mode=props.rig_bone_mode)

            # Write vertex groups onto the Blender object
            for bname, vmap in weights_map.items():
                vg = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)
                for vi, w in vmap.items():
                    vg.add([vi], w, "REPLACE")

            for m in disabled_mods:
                m.show_viewport = True

            bind_mesh_to_arma3_skeleton(obj, arm)
            if props.rig_bone_mode in ("AUTO", "MANNEQUIN_KD") and props.gear_type not in ("HEADGEAR", "GOGGLES", "FACE"):
                try:
                    if len(obj.vertex_groups) > 0:
                        obj.vertex_groups.active_index = 0
                    bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                    bpy.ops.object.vertex_group_smooth(
                        group_select_mode="BONE_DEFORM",
                        factor=0.4,
                        repeat=2,
                    )
                    bpy.ops.object.vertex_group_limit_total(
                        group_select_mode="BONE_DEFORM",
                        limit=4,
                    )
                    bpy.ops.object.vertex_group_normalize_all(
                        group_select_mode="BONE_DEFORM",
                        lock_active=False,
                    )
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    if context.mode != "OBJECT":
                        try:
                            bpy.ops.object.mode_set(mode="OBJECT")
                        except Exception:
                            pass
            bones_list = ", ".join(weights_map.keys())

        # Bind to Arma3_Skeleton so posing moves the gear alongside the mannequin
        bind_mesh_to_arma3_skeleton(obj, arm)

        # Restore previous pose on Arma3_Skeleton so user sees deformation immediately
        if arm and arm.pose and saved_pose:
            for pb in arm.pose.bones:
                if pb.name in saved_pose:
                    rmode, reul, rquat, rloc = saved_pose[pb.name]
                    pb.rotation_mode = rmode
                    pb.rotation_euler = reul
                    pb.rotation_quaternion = rquat
                    pb.location = rloc

        # Also enable ArmaToolbox properties nicely
        if hasattr(obj, "armaObjProps"):
            obj.armaObjProps.isArmaObject = True

        method_lbl = "Robust Weight Transfer + Smooth" if used_rwt else "Auto-Weights"
        self.report(
            {"INFO"},
            f"Готово ({method_lbl})! Кости: {bones_list or 'head'}.",
        )
        return {"FINISHED"}


class EZMOD_OT_fix_multi_uv(bpy.types.Operator):
    bl_idname = "ezmod.fix_multi_uv"
    bl_label = "🔧 Починить мульти-UV / Склеить ассеты"
    bl_description = (
        "Если на шлеме/бронике несколько ассетов или несколько UV-карт (после Ctrl+J) и плывут текстуры — "
        "склеивает все UV-карты в единую 'UVMap' с сохранением материалов каждого ассета"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        target_objs = [
            o
            for o in context.selected_objects
            if not _is_helper_scene_obj(o)
        ]
        if not target_objs and context.active_object and not _is_helper_scene_obj(context.active_object):
            target_objs = [context.active_object]

        if not target_objs:
            self.report({"ERROR"}, "Выделите вашу модель (или несколько деталей шлема/броника)!")
            return {"CANCELLED"}

        # First: on every selected object, consolidate any split UV maps into one clean 'UVMap'
        for o in target_objs:
            me = o.data
            if not me.uv_layers:
                me.uv_layers.new(name="UVMap")
                continue

            if len(me.uv_layers) > 1:
                bm = bmesh.new()
                bm.from_mesh(me)
                bm.faces.ensure_lookup_table()
                uv_layers = list(bm.loops.layers.uv.values())
                primary_uv = bm.loops.layers.uv.active or uv_layers[0]
                for f in bm.faces:
                    best_uvs = resolve_best_face_uvs(f, uv_layers, primary_uv)
                    for loop, (u, v) in zip(f.loops, best_uvs):
                        loop[primary_uv].uv = (u, v)
                bm.to_mesh(me)
                bm.free()

                active_name = me.uv_layers.active.name
                to_remove = [layer.name for layer in me.uv_layers if layer.name != active_name]
                for rname in to_remove:
                    layer = me.uv_layers.get(rname)
                    if layer:
                        me.uv_layers.remove(layer)

            me.uv_layers[0].name = "UVMap"
            me.uv_layers.active_index = 0
            me.uv_layers[0].active_render = True

            # Also update any Shader UVMap nodes to point to 'UVMap'
            for slot in o.material_slots:
                mat = slot.material
                if mat and getattr(mat, "use_nodes", False) and mat.node_tree:
                    for node in mat.node_tree.nodes:
                        if node.type == "UVMAP":
                            node.uv_map = "UVMap"

        # If multiple separate sub-assets were selected, join them cleanly now that all have 'UVMap'
        if len(target_objs) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for o in target_objs:
                o.select_set(True)
            context.view_layer.objects.active = target_objs[0]
            bpy.ops.object.join()
            self.report(
                {"INFO"},
                f"Сшито {len(target_objs)} ассетов в единую модель с общей картой 'UVMap' и раздельными материалами!",
            )
        else:
            self.report({"INFO"}, "Мульти-UV карты успешно склеены в единую 'UVMap'! Текстуры выровнены.")

        return {"FINISHED"}


class EZMOD_OT_adapt_dxt5nm_normals(bpy.types.Operator):
    bl_idname = "ezmod.adapt_dxt5nm_normals"
    bl_label = "🧪 Адаптировать Тарков нормали и Альфу (0.45)"
    bl_description = (
        "Распаковывает все DXT5nm карты нормалей Таркова в стандартные синие (Tangent Space), "
        "заменяет ноды EFT_DXT5nm_Normal на стандартный Normal Map со значением Strength = 0.45, "
        "и отключает паразитную прозрачность Альфа-канала на цветных текстурах (_D / _diff)"
    )
    bl_options = {"REGISTER", "UNDO"}

    normal_strength: bpy.props.FloatProperty(
        name="Сила нормалей (Strength)",
        default=0.45,
        min=0.05,
        max=2.0,
    )

    def execute(self, context):
        import importlib
        importlib.reload(ez_mod_core)

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        target_objs = [o for o in context.selected_objects if not _is_helper_scene_obj(o)]
        if not target_objs and context.active_object and not _is_helper_scene_obj(context.active_object):
            target_objs = [context.active_object]

        if not target_objs:
            self.report({"ERROR"}, "Выделите 3D-модель со снаряжением!")
            return {"CANCELLED"}

        converted_count = 0
        strength_updated = 0
        alpha_fixed = 0
        mats_to_check = set()
        for o in target_objs:
            for slot in o.material_slots:
                if slot.material and slot.material.node_tree:
                    mats_to_check.add(slot.material)

        for mat in mats_to_check:
            nt = mat.node_tree
            group_node = None
            tex_node = None

            # 0. Fix Tarkov Diffuse Alpha -> Principled BSDF.Alpha links (where Alpha is glossiness, not transparency!)
            for n in nt.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    if "Alpha" in n.inputs and n.inputs["Alpha"].is_linked:
                        for l in list(n.inputs["Alpha"].links):
                            nt.links.remove(l)
                        n.inputs["Alpha"].default_value = 1.0
                        alpha_fixed += 1
                    if "Base Color" in n.inputs and n.inputs["Base Color"].is_linked:
                        c_from = n.inputs["Base Color"].links[0].from_node
                        if c_from.type == "TEX_IMAGE" and c_from.image:
                            try:
                                c_from.image.alpha_mode = "CHANNEL_PACKED"
                            except Exception:
                                pass

            # 1. Look for EFT_DXT5nm or any GROUP node connected to Principled BSDF.Normal
            for n in nt.nodes:
                if n.type == "GROUP" and n.node_tree:
                    tname = n.node_tree.name.lower()
                    is_norm_grp = any(k in tname for k in ("dxt5", "eft", "unity", "norm"))
                    if not is_norm_grp:
                        for l in nt.links:
                            if l.from_node == n and l.to_socket.name == "Normal":
                                is_norm_grp = True
                                break
                    if is_norm_grp:
                        group_node = n
                        for in_sock in n.inputs:
                            for l in in_sock.links:
                                if l.from_node.type == "TEX_IMAGE" and l.from_node.image:
                                    tex_node = l.from_node
                                    break
                            if tex_node:
                                break
                        break

            # 2. If no group node, check any TEX_IMAGE normal map that might be DXT5nm
            if not tex_node:
                for n in nt.nodes:
                    if n.type == "TEX_IMAGE" and n.image:
                        fp = bpy.path.abspath(n.image.filepath) if n.image.filepath else ""
                        if fp and os.path.exists(fp):
                            nl = (n.name + " " + n.image.name + " " + os.path.basename(fp)).lower()
                            if any(k in nl for k in ("norm", "nohq", "_nrm", "nrm_", "_n.", "_n_", "_nml")):
                                tex_node = n
                                break

            if tex_node and tex_node.image:
                raw_fp = bpy.path.abspath(tex_node.image.filepath) if tex_node.image.filepath else ""
                if raw_fp and os.path.exists(raw_fp):
                    clean_fp = ez_mod_core.unpack_dxt5nm_if_needed(raw_fp)
                    if os.path.exists(clean_fp):
                        if clean_fp != raw_fp:
                            new_img = bpy.data.images.load(clean_fp, check_existing=True)
                            tex_node.image = new_img
                            converted_count += 1
                        try:
                            tex_node.image.colorspace_settings.name = "Non-Color"
                        except Exception:
                            pass

                        if group_node:
                            target_socket = None
                            for l in nt.links:
                                if l.from_node == group_node and l.from_socket.name in ("Normal", "Vector"):
                                    target_socket = l.to_socket
                                    break
                            if target_socket is None:
                                bsdf = next((nd for nd in nt.nodes if nd.type == "BSDF_PRINCIPLED"), None)
                                if bsdf and "Normal" in bsdf.inputs:
                                    target_socket = bsdf.inputs["Normal"]
                            norm_node = nt.nodes.new(type="ShaderNodeNormalMap")
                            norm_node.location = group_node.location
                            norm_node.space = "TANGENT"
                            if "Strength" in norm_node.inputs:
                                norm_node.inputs["Strength"].default_value = self.normal_strength
                            nt.links.new(tex_node.outputs["Color"], norm_node.inputs["Color"])
                            if target_socket:
                                nt.links.new(norm_node.outputs["Normal"], target_socket)
                            nt.nodes.remove(group_node)
                            converted_count += 1

            # 3. Set Strength = 0.45 on all ShaderNodeNormalMap nodes in this material
            for n in nt.nodes:
                if n.type == "NORMAL_MAP" and "Strength" in n.inputs:
                    if abs(n.inputs["Strength"].default_value - self.normal_strength) > 1e-4:
                        n.inputs["Strength"].default_value = self.normal_strength
                        strength_updated += 1

        self.report(
            {"INFO"},
            f"✅ Готово! Материалов проверено: {len(mats_to_check)} (DXT5nm распаковано: {converted_count}, Strength=0.45 выставлен: {strength_updated}, Альфа почищена: {alpha_fixed})",
        )
        return {"FINISHED"}


class EZMOD_OT_build_mod(bpy.types.Operator):
    bl_idname = "ezmod.build_mod"
    bl_label = "🚀 СОБРАТЬ МОД В ARMA 3 (1-CLICK PBO)"
    bl_description = "Автоматически экспортирует выбранную модель (со всеми материалами и UV ассетов), конвертирует текстуры в PAA, компилирует скелет ODOL + config.bin и пакует в @EZ_Custom_Gear"

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        target_objs = [
            o
            for o in context.selected_objects
            if not _is_helper_scene_obj(o)
        ]
        if not target_objs and context.active_object and not _is_helper_scene_obj(context.active_object):
            target_objs = [context.active_object]

        if not target_objs:
            self.report({"ERROR"}, "Выделите вашу 3D-модель снаряжения (не манекен)!")
            return {"CANCELLED"}

        main_obj = context.active_object if context.active_object in target_objs else target_objs[0]
        props = context.scene.ez_mod_props

        def collect_from_objects(objs_list):
            muted_arm_mods = []
            for o in objs_list:
                for m in o.modifiers:
                    if m.type == "ARMATURE" and m.show_viewport:
                        m.show_viewport = False
                        muted_arm_mods.append(m)
            context.view_layer.update()

            depsgraph = context.evaluated_depsgraph_get()
            verts_arma = []
            pts_bl = []
            nrms_arma = []
            faces_arma = []
            vgroups = {"camo": {}}
            material_slots_tex = []
            mat_key_to_slot_idx = {}

            override_diff = bpy.path.abspath(props.diffuse_tex) if props.diffuse_tex else ""
            has_override = bool(override_diff and os.path.exists(override_diff))

            try:
                for o in objs_list:
                    local_to_global_mat = {}
                    if has_override or len(o.material_slots) == 0:
                        d_p, n_p = detect_textures_from_object(o, props)
                        mkey = (d_p or "", n_p or "")
                        if mkey not in mat_key_to_slot_idx:
                            mat_key_to_slot_idx[mkey] = len(material_slots_tex)
                            material_slots_tex.append((d_p, n_p))
                        for s_i in range(max(1, len(o.material_slots))):
                            local_to_global_mat[s_i] = mat_key_to_slot_idx[mkey]
                    else:
                        for s_i, slot in enumerate(o.material_slots):
                            d_p, n_p = extract_slot_textures(slot.material)
                            g_info = detect_glass_material_info(slot.material, d_p)
                            mkey = (d_p or "", n_p or "", slot.material.name if slot.material else str(s_i), bool(g_info))
                            if mkey not in mat_key_to_slot_idx:
                                mat_key_to_slot_idx[mkey] = len(material_slots_tex)
                                material_slots_tex.append((d_p, n_p, g_info))
                            local_to_global_mat[s_i] = mat_key_to_slot_idx[mkey]

                    eval_obj = o.evaluated_get(depsgraph)
                    temp_mesh = eval_obj.to_mesh()
                    bm = bmesh.new()
                    bm.from_mesh(temp_mesh)
                    bmesh.ops.transform(bm, matrix=o.matrix_world, verts=bm.verts)
                    bmesh.ops.triangulate(bm, faces=bm.faces)
                    bm.verts.ensure_lookup_table()
                    bm.faces.ensure_lookup_table()

                    uv_layers = list(bm.loops.layers.uv.values())
                    primary_uv = bm.loops.layers.uv.active or (uv_layers[0] if uv_layers else None)

                    vert_offset = len(verts_arma)
                    obj_pts_bl = []
                    for v in bm.verts:
                        pt = (v.co.x, v.co.y, v.co.z)
                        pts_bl.append(pt)
                        obj_pts_bl.append(pt)
                        verts_arma.append((v.co.x, v.co.z, v.co.y))
                        nrms_arma.append((-v.normal.x, -v.normal.z, -v.normal.y))
                        vgroups["camo"][vert_offset + v.index] = 1.0

                    arma_vg_map = {
                        vg.index: vg.name.lower()
                        for vg in o.vertex_groups
                        if vg.name.lower() in ARMA3_BONE_NAMES_SET
                    }
                    has_painted_weights = False
                    if arma_vg_map and len(temp_mesh.vertices) == len(bm.verts):
                        for local_vi, mv in enumerate(temp_mesh.vertices):
                            for g in mv.groups:
                                bname = arma_vg_map.get(g.group)
                                if bname and g.weight > 0.001:
                                    has_painted_weights = True
                                    vgroups.setdefault(bname, {})[vert_offset + local_vi] = float(g.weight)

                    if not has_painted_weights:
                        auto_vg = compute_auto_weights_for_points(obj_pts_bl, props.gear_type, bone_mode=props.rig_bone_mode)
                        for bname, wmap in auto_vg.items():
                            if bname == "camo":
                                continue
                            dest = vgroups.setdefault(bname, {})
                            for local_vi, w in wmap.items():
                                dest[vert_offset + local_vi] = w

                    for f in bm.faces:
                        v_idxs = [vert_offset + loop.vert.index for loop in f.loops]
                        n_idxs = list(v_idxs)
                        uvs = resolve_best_face_uvs(f, uv_layers, primary_uv)
                        g_mat_idx = local_to_global_mat.get(f.material_index, 0)
                        faces_arma.append((v_idxs, n_idxs, uvs, g_mat_idx))

                    bm.free()
                    eval_obj.to_mesh_clear()
            finally:
                for m in muted_arm_mods:
                    m.show_viewport = True

            if props.gear_type == "FACE" and "personality" not in vgroups and verts_arma:
                vgroups["personality"] = {i: 1.0 for i in range(len(verts_arma))}
            return verts_arma, nrms_arma, faces_arma, vgroups, material_slots_tex

        slug = props.class_slug.strip() or props.item_name.strip() or main_obj.name

        try:
            import importlib
            importlib.reload(ez_mod_core)
            aceax_opts_list = []
            sweep_axes = []
            for opt in props.aceax_options:
                oid = opt.option_id.strip() or "Option"
                vals = [v.value.strip() for v in opt.values if v.value.strip()]
                if not vals:
                    vals = ["Base"]
                sel_idx = min(max(0, opt.active_value_idx), len(vals) - 1)
                aceax_opts_list.append({
                    "id": oid,
                    "label": oid,
                    "values": vals,
                    "selected": vals[sel_idx],
                })
                snapped = [
                    (oid, (v.value.strip() or f"Var{vi + 1}"), v.scene_snapshot)
                    for vi, v in enumerate(opt.values)
                    if v.scene_snapshot
                ]
                if len(snapped) >= 2:
                    sweep_axes.append(snapped)
                else:
                    cur_v = opt.values[sel_idx] if opt.values else None
                    cur_name = (cur_v.value.strip() if cur_v else "") or vals[sel_idx]
                    cur_snap = cur_v.scene_snapshot if cur_v else ""
                    sweep_axes.append([(oid, cur_name, cur_snap)])

            aceax_cfg = {
                "enabled": bool(props.aceax_enabled),
                "model": props.aceax_model.strip() or "EZ_Custom_Gear_Pack",
                "label": props.aceax_label.strip() or (props.item_name.strip() or main_obj.name),
                "camo": props.aceax_camo.strip() or "Default",
                "variant": props.aceax_variant.strip() or "Base",
                "options": aceax_opts_list,
            }

            has_multi = bool(props.aceax_enabled and props.gear_type != "FACE" and any(len(ax) >= 2 for ax in sweep_axes))
            if has_multi:
                combos = list(itertools.product(*sweep_axes))[:32]
                orig_snap = _capture_gear_scene_snapshot(context)
                variants_payload = []
                try:
                    for combo in combos:
                        sel_map = {}
                        for oid, vname, snap in combo:
                            sel_map[oid] = vname
                            if snap:
                                _apply_gear_scene_snapshot(context, snap)
                        vis_objs = [o for o in context.scene.objects if not _is_helper_scene_obj(o) and not o.hide_get()]
                        if not vis_objs:
                            vis_objs = target_objs
                        va, na, fa, vg, mslots = collect_from_objects(vis_objs)
                        variants_payload.append({
                            "vertices": va,
                            "normals": na,
                            "faces": fa,
                            "vertex_groups": vg,
                            "material_slots": mslots,
                            "aceax_selections": sel_map,
                        })
                finally:
                    _apply_gear_scene_snapshot(context, orig_snap)

                has_empty_vp = bool(bpy.data.objects.get("LOD_View_Pilot_Empty")) or props.hide_in_first_person
                res = ez_mod_core.build_ez_mod_variants(
                    slug=slug,
                    display_name=props.item_name.strip() or main_obj.name,
                    gear_type=props.gear_type,
                    variants=variants_payload,
                    author=props.author_name.strip() or "EZ_MOD Creator",
                    armor=props.armor_val,
                    mass=props.mass_val,
                    max_load=props.load_val,
                    aceax_cfg=aceax_cfg,
                    mod_folder_name=props.mod_folder,
                    empty_view_pilot=has_empty_vp,
                )
                vcount = res.get("variant_count", len(variants_payload))
                props.last_status = f"✅ Собрано {vcount} вар. в 1 PBO: {res['class_name']}"
                self.report(
                    {"INFO"},
                    f"МОД ГОТОВ! '{res['display_name']}' ({vcount} вариантов в 1 PBO) -> {res['pbo_path']}!",
                )
                return {"FINISHED"}

            verts_arma, nrms_arma, faces_arma, vgroups, material_slots_tex = collect_from_objects(target_objs)
            diff_img = material_slots_tex[0][0] if material_slots_tex else None
            norm_img = material_slots_tex[0][1] if material_slots_tex else None
            has_empty_vp = bool(bpy.data.objects.get("LOD_View_Pilot_Empty")) or props.hide_in_first_person
            res = ez_mod_core.build_ez_mod(
                slug=slug,
                display_name=props.item_name.strip() or main_obj.name,
                gear_type=props.gear_type,
                vertices=verts_arma,
                normals=nrms_arma,
                faces=faces_arma,
                vertex_groups=vgroups,
                diffuse_img_path=diff_img,
                normal_img_path=norm_img,
                author=props.author_name.strip() or "EZ_MOD Creator",
                armor=props.armor_val,
                mass=props.mass_val,
                max_load=props.load_val,
                material_slots=material_slots_tex,
                aceax_cfg=aceax_cfg,
                mod_folder_name=props.mod_folder,
                empty_view_pilot=has_empty_vp,
            )
        except Exception as e:
            self.report({"ERROR"}, f"Ошибка сборки: {e}")
            return {"CANCELLED"}

        props.last_status = f"✅ Собрано ({len(material_slots_tex)} мат.): {res['class_name']}"
        self.report(
            {"INFO"},
            f"МОД ГОТОВ! '{res['display_name']}' ({len(material_slots_tex)} мат., класс {res['class_name']}) -> {res['pbo_path']}!",
        )
        return {"FINISHED"}


class EZMOD_OT_aceax_add_option(bpy.types.Operator):
    bl_idname = "ezmod.aceax_add_option"
    bl_label = "+ Добавить ID категории"
    bl_description = "Добавляет новую категорию в Customizable (например: Camo, NVG, Headset, Gloves)"

    def execute(self, context):
        props = context.scene.ez_mod_props
        item = props.aceax_options.add()
        idx = len(props.aceax_options)
        defaults = ["Camo", "Variant", "NVG", "Headset", "Patches"]
        item.option_id = defaults[idx - 1] if idx <= len(defaults) else f"Option{idx}"
        v1 = item.values.add()
        v1.value = "Base" if idx > 1 else "Default"
        item.active_value_idx = 0
        return {"FINISHED"}


class EZMOD_OT_aceax_remove_option(bpy.types.Operator):
    bl_idname = "ezmod.aceax_remove_option"
    bl_label = "-"
    bl_description = "Удалить эту категорию"
    opt_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            props.aceax_options.remove(self.opt_index)
        return {"FINISHED"}


class EZMOD_OT_aceax_add_value(bpy.types.Operator):
    bl_idname = "ezmod.aceax_add_value"
    bl_label = "+ Вариант"
    bl_description = "Добавить новый вариант (кнопку) в эту категорию"
    opt_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            opt = props.aceax_options[self.opt_index]
            v = opt.values.add()
            v.value = f"Var{len(opt.values)}"
            new_idx = len(opt.values) - 1
            opt.active_value_idx = new_idx
            opt.preview_enabled = True
            _reset_option_shape_keys(context, self.opt_index)
            sk_tag = f"EZ_Var_{self.opt_index}_{new_idx}"
            for obj in context.scene.objects:
                if _is_helper_scene_obj(obj):
                    continue
                if obj.type == "MESH" and obj.data is not None and obj.data.shape_keys is not None:
                    sk = obj.data.shape_keys
                    kb = sk.key_blocks.get(sk_tag) or obj.shape_key_add(name=sk_tag, from_mix=False)
                    kb.value = 1.0
                    idx = sk.key_blocks.find(sk_tag)
                    if idx >= 0:
                        obj.active_shape_key_index = idx
                    obj.data.update()
        return {"FINISHED"}


class EZMOD_OT_aceax_remove_value(bpy.types.Operator):
    bl_idname = "ezmod.aceax_remove_value"
    bl_label = "-"
    bl_description = "Удалить этот вариант"
    opt_index: bpy.props.IntProperty()
    val_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            opt = props.aceax_options[self.opt_index]
            if 0 <= self.val_index < len(opt.values):
                opt.values.remove(self.val_index)
                if opt.active_value_idx >= len(opt.values):
                    opt.active_value_idx = max(0, len(opt.values) - 1)
        return {"FINISHED"}


def _is_helper_scene_obj(obj):
    if not obj or obj.type != "MESH":
        return True
    name = obj.name
    if name == "Arma3_Mannequin" or name.startswith("Ref_") or name.startswith("LOD_View_Pilot"):
        return True
    if any(name.endswith(sfx) for sfx in ("_LOD2", "_LOD3", "_LOD4", "_LOD5", "_Shadow", "_Geometry")):
        return True
    return False


def _capture_gear_scene_snapshot(context, shape_key_tag=None):
    """Captures visibility, transforms, material slots, and optional Shape Key of all gear meshes."""
    if shape_key_tag and context.active_object and context.active_object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    depsgraph = context.evaluated_depsgraph_get() if shape_key_tag else None
    data = {}
    for obj in context.scene.objects:
        if _is_helper_scene_obj(obj):
            continue

        entry = {
            "visible": not obj.hide_get(),
            "loc": [float(v) for v in obj.location],
            "rot_mode": str(obj.rotation_mode),
            "rot_euler": [float(v) for v in obj.rotation_euler],
            "rot_quat": [float(v) for v in obj.rotation_quaternion],
            "scale": [float(v) for v in obj.scale],
            "mats": [s.material.name if s.material else None for s in obj.material_slots],
        }

        if obj.type == "MESH" and obj.data is not None:
            if shape_key_tag:
                n_verts = len(obj.data.vertices)
                if n_verts > 0:
                    coords = [0.0] * (n_verts * 3)
                    got_eval = False
                    if depsgraph is not None:
                        eval_obj = obj.evaluated_get(depsgraph)
                        eval_mesh = eval_obj.to_mesh()
                        try:
                            if len(eval_mesh.vertices) == n_verts:
                                eval_mesh.vertices.foreach_get("co", coords)
                                got_eval = True
                        finally:
                            eval_obj.to_mesh_clear()
                    if not got_eval:
                        obj.data.vertices.foreach_get("co", coords)

                    if obj.data.shape_keys is None:
                        obj.shape_key_add(name="Basis", from_mix=False)
                    sk = obj.data.shape_keys
                    kb = sk.key_blocks.get(shape_key_tag)
                    if kb is None:
                        kb = obj.shape_key_add(name=shape_key_tag, from_mix=False)
                    kb.data.foreach_set("co", coords)
                    for other_kb in sk.key_blocks:
                        if other_kb.name.startswith("EZ_Var_"):
                            other_kb.value = 1.0 if other_kb.name == shape_key_tag else 0.0
                    idx = sk.key_blocks.find(shape_key_tag)
                    if idx >= 0:
                        obj.active_shape_key_index = idx
                    obj.data.update()
                    entry["shape_key"] = shape_key_tag
            elif obj.data.shape_keys is not None:
                entry["shape_weights"] = {
                    kb.name: float(kb.value) for kb in obj.data.shape_keys.key_blocks
                }

        data[obj.name] = entry
    return json.dumps(data, ensure_ascii=False)


def _apply_gear_scene_snapshot(context, snap_json):
    """Applies saved visibility, transforms, material slots, and Shape Keys to scene gear meshes."""
    if not snap_json:
        return False
    try:
        data = json.loads(snap_json)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False

    if context.active_object and context.active_object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    first_visible = None
    for obj in context.scene.objects:
        if _is_helper_scene_obj(obj):
            continue
        info = data.get(obj.name)
        if not isinstance(info, dict):
            continue
        vis = bool(info.get("visible", True))
        obj.hide_set(not vis)
        if vis and first_visible is None:
            first_visible = obj
        if isinstance(info.get("loc"), list) and len(info["loc"]) == 3:
            obj.location = info["loc"]
        if isinstance(info.get("rot_mode"), str):
            obj.rotation_mode = info["rot_mode"]
        if isinstance(info.get("rot_euler"), list) and len(info["rot_euler"]) == 3:
            obj.rotation_euler = info["rot_euler"]
        if isinstance(info.get("rot_quat"), list) and len(info["rot_quat"]) == 4:
            obj.rotation_quaternion = info["rot_quat"]
        if isinstance(info.get("scale"), list) and len(info["scale"]) == 3:
            obj.scale = info["scale"]
        mats = info.get("mats")
        if isinstance(mats, list) and len(mats) == len(obj.material_slots):
            for idx, mname in enumerate(mats):
                if mname and mname in bpy.data.materials:
                    obj.material_slots[idx].material = bpy.data.materials[mname]

        if obj.type == "MESH" and obj.data is not None and obj.data.shape_keys is not None:
            sk = obj.data.shape_keys
            target_sk = info.get("shape_key")
            shape_weights = info.get("shape_weights")
            if isinstance(target_sk, str):
                cat_prefix = target_sk.rsplit("_", 1)[0] + "_" if target_sk.startswith("EZ_Var_") else "EZ_Var_"
                for kb in sk.key_blocks:
                    if kb.name.startswith(cat_prefix):
                        kb.value = 1.0 if kb.name == target_sk else 0.0
                idx = sk.key_blocks.find(target_sk)
                if idx >= 0:
                    obj.active_shape_key_index = idx
                obj.data.update()
            elif isinstance(shape_weights, dict):
                for kb in sk.key_blocks:
                    if kb.name in shape_weights:
                        kb.value = float(shape_weights[kb.name])
                obj.data.update()

    act = context.view_layer.objects.active
    if first_visible and (act is None or act.hide_get() or _is_helper_scene_obj(act)):
        context.view_layer.objects.active = first_visible
        first_visible.select_set(True)
    return True


def _reset_option_shape_keys(context, opt_index):
    """Turns off all EZ_Var_<opt_index>_* shape keys (returns to Basis without deleting)."""
    if context.active_object and context.active_object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
    prefix = f"EZ_Var_{opt_index}_"
    for obj in context.scene.objects:
        if _is_helper_scene_obj(obj):
            continue
        if obj.type == "MESH" and obj.data is not None and obj.data.shape_keys is not None:
            changed = False
            for kb in obj.data.shape_keys.key_blocks:
                if kb.name.startswith(prefix) and kb.value != 0.0:
                    kb.value = 0.0
                    changed = True
            if changed:
                obj.data.update()


class EZMOD_OT_aceax_capture_view(bpy.types.Operator):
    bl_idname = "ezmod.aceax_capture_view"
    bl_label = "Запомнить / Обновить вид варианта"
    bl_description = "Сохраняет текущую геометрию (вкл. Edit Mode через Shape Key), видимость, трансформации и материалы для этого варианта"
    opt_index: bpy.props.IntProperty()
    val_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            opt = props.aceax_options[self.opt_index]
            if 0 <= self.val_index < len(opt.values):
                val_item = opt.values[self.val_index]
                sk_tag = f"EZ_Var_{self.opt_index}_{self.val_index}"
                val_item.scene_snapshot = _capture_gear_scene_snapshot(context, shape_key_tag=sk_tag)
                opt.active_value_idx = self.val_index
                opt.preview_enabled = True
                self.report({"INFO"}, f"Вид сохранён для варианта '{val_item.value}'")
        return {"FINISHED"}


class EZMOD_OT_aceax_clear_view(bpy.types.Operator):
    bl_idname = "ezmod.aceax_clear_view"
    bl_label = "Удалить сохранённый вид"
    bl_description = "Удаляет сохранённый вид и Shape Key этого варианта"
    opt_index: bpy.props.IntProperty()
    val_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            opt = props.aceax_options[self.opt_index]
            if 0 <= self.val_index < len(opt.values):
                val_item = opt.values[self.val_index]
                val_item.scene_snapshot = ""
                sk_tag = f"EZ_Var_{self.opt_index}_{self.val_index}"
                for obj in context.scene.objects:
                    if obj.type == "MESH" and obj.data is not None and obj.data.shape_keys is not None:
                        kb = obj.data.shape_keys.key_blocks.get(sk_tag)
                        if kb is not None:
                            obj.shape_key_remove(kb)
                self.report({"INFO"}, f"Вид для варианта '{val_item.value}' удалён")
        return {"FINISHED"}


class EZMOD_OT_aceax_select_value(bpy.types.Operator):
    bl_idname = "ezmod.aceax_select_value"
    bl_label = "Вкл / Выкл просмотр варианта"
    bl_description = "Включает вид варианта; повторное нажатие отключает его (откат к базе без удаления)"
    opt_index: bpy.props.IntProperty()
    val_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.ez_mod_props
        if 0 <= self.opt_index < len(props.aceax_options):
            opt = props.aceax_options[self.opt_index]
            if 0 <= self.val_index < len(opt.values):
                if opt.active_value_idx == self.val_index and opt.preview_enabled:
                    opt.preview_enabled = False
                    _reset_option_shape_keys(context, self.opt_index)
                else:
                    opt.active_value_idx = self.val_index
                    opt.preview_enabled = True
                    _reset_option_shape_keys(context, self.opt_index)
                    snap = opt.values[self.val_index].scene_snapshot
                    if snap:
                        _apply_gear_scene_snapshot(context, snap)
                    else:
                        # Ensure a non-Basis Shape Key is active so Edit Mode never mutates Basis
                        sk_tag = f"EZ_Var_{self.opt_index}_{self.val_index}"
                        for obj in context.scene.objects:
                            if _is_helper_scene_obj(obj):
                                continue
                            if obj.type == "MESH" and obj.data is not None and obj.data.shape_keys is not None:
                                sk = obj.data.shape_keys
                                kb = sk.key_blocks.get(sk_tag)
                                if kb is None:
                                    kb = obj.shape_key_add(name=sk_tag, from_mix=False)
                                kb.value = 1.0
                                idx = sk.key_blocks.find(sk_tag)
                                if idx >= 0:
                                    obj.active_shape_key_index = idx
                                obj.data.update()
        return {"FINISHED"}


class EZMOD_OT_create_mod_folder(bpy.types.Operator):
    bl_idname = "ezmod.create_mod_folder"
    bl_label = "Создать папку @Мода"
    bl_description = "Создаёт новую папку @Мода в !Workshop (addons/, mod.cpp, meta.cpp) и выбирает её для сборки"

    new_name: bpy.props.StringProperty(
        name="Имя мода",
        description="Название папки (например: @My_Squad_Gear)",
        default="@My_Custom_Mod",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.ez_mod_props
        mod_dir, addons_dir, clean_name = ez_mod_core.resolve_mod_dirs(self.new_name)
        ez_mod_core.ensure_mod_metadata(
            mod_dir, addons_dir, clean_name, author=props.author_name.strip() or "EZ_MOD Builder"
        )
        props.mod_folder = clean_name
        self.report({"INFO"}, f"Папка мода создана и выбрана: {clean_name}")
        return {"FINISHED"}


class EZMOD_OT_open_mod_folder(bpy.types.Operator):
    bl_idname = "ezmod.open_mod_folder"
    bl_label = "📂 Открыть папку мода"
    bl_description = "Открывает выбранную папку @Мода в проводнике Windows"

    def execute(self, context):
        props = context.scene.ez_mod_props
        mod_dir, addons_dir, clean_name = ez_mod_core.resolve_mod_dirs(props.mod_folder)
        ez_mod_core.ensure_mod_metadata(
            mod_dir, addons_dir, clean_name, author=props.author_name.strip() or "EZ_MOD Builder"
        )
        os.startfile(mod_dir)
        return {"FINISHED"}


class EZMOD_OT_set_gear_type(bpy.types.Operator):
    bl_idname = "ezmod.set_gear_type"
    bl_label = "Выбрать тип предмета (Слот)"
    bl_description = "Задаёт, в какой слот Arma 3 пойдёт эта модель (Шлем, Броник, Рюкзак, Очки, ПНВ, Лицо)"
    bl_options = {"REGISTER", "UNDO"}

    gear_type: bpy.props.StringProperty(default="HEADGEAR")

    def execute(self, context):
        props = context.scene.ez_mod_props
        props.gear_type = self.gear_type
        if self.gear_type in ("HEADGEAR", "GOGGLES"):
            props.hide_in_first_person = True
        return {"FINISHED"}


def draw_ez_mod_ui(layout, context):
    props = context.scene.ez_mod_props
    obj = context.active_object
    if _is_helper_scene_obj(obj):
        obj = _resolve_target_gear_mesh(context)

    # --- Block 1: Mannequin & Fitting ---
    box1 = layout.box()
    box1.label(text="1. Манекен и Скелет (Веса / Позы)", icon="ARMATURE_DATA")
    col1 = box1.column(align=True)
    col1.scale_y = 1.25
    col1.operator("ezmod.spawn_mannequin", icon="OUTLINER_OB_ARMATURE")
    row1 = box1.row(align=True)
    row1.operator("ezmod.toggle_mannequin", text="👁 Скрыть/Показать")
    row1.operator("ezmod.spawn_ref_gear", text="🎯 Эталон габаритов")
    row_pose = box1.row(align=True)
    row_pose.operator("ezmod.test_pose", text="🤸 Тест позы (Сгиб)", icon="POSE_HLT")
    row_pose.operator("ezmod.reset_pose", text="🔄 Т-Поза", icon="LOOP_BACK")

    # --- Block 2: Item Configuration ---
    box2 = layout.box()
    box2.label(text="2. Настройка предмета в Арсенале", icon="MODIFIER")

    # 1-Click Model Import Box (from ArmaModManager Inspector or local .p3d)
    imp_box = box2.box()
    imp_box.label(text="📥 Импорт модели в Blender (со всеми настройками):", icon="IMPORT")
    imp_col = imp_box.column(align=True)
    imp_col.prop(props, "bridge_selected_model", text="")
    imp_row = imp_col.row(align=True)
    imp_row.scale_y = 1.35
    imp_row.operator("ezmod.import_selected_model", text="📥 Импорт выбранной модели", icon="IMPORT")
    imp_row.operator("ezmod.import_p3d_file", text="📂 Из .p3d", icon="FILE_FOLDER")

    # Prominent 3D Model Selector Box
    mdl_box = box2.box()
    if obj and not _is_helper_scene_obj(obj):
        num_mats = max(1, len(obj.material_slots))
        num_uvs = len(obj.data.uv_layers)
        mdl_hdr = mdl_box.row(align=True)
        mdl_hdr.label(
            text=f"🎯 3D-Модель: {obj.name} ({len(obj.data.polygons)} пол. | {num_mats} мат. | {num_uvs} UV)",
            icon="CHECKMARK",
        )
        mdl_pick = mdl_box.row(align=True)
        mdl_pick.scale_y = 1.25
        mdl_pick.prop(props, "scene_gear_mesh", text="Выбрать объект")
        if num_uvs > 1:
            warn_row = mdl_box.row()
            warn_row.alert = True
            warn_row.label(text=f"Обнаружено {num_uvs} UV-карты! Нажми 'Починить мульти-UV'", icon="UV")
    else:
        status_row = mdl_box.row()
        status_row.alert = True
        status_row.scale_y = 1.3
        status_row.label(text="⚠ ВЫДЕЛИТЕ 3D-МОДЕЛЬ СНАРЯГИ В СЦЕНЕ!", icon="ERROR")
        mdl_pick = mdl_box.row(align=True)
        mdl_pick.scale_y = 1.3
        mdl_pick.prop(props, "scene_gear_mesh", text="Выбрать объект")

    # PROMINENT GEAR TYPE ("ЧТО ЭТО?") SELECTOR BOX
    slot_box = box2.box()
    slot_hdr = slot_box.row(align=True)
    slot_hdr.scale_y = 1.2
    slot_hdr.label(text="🎯 ЧТО ЭТО ЗА ПРЕДМЕТ? (Выбери слот):", icon="OUTLINER_OB_ARMATURE")

    slot_grid = slot_box.column(align=True)
    r_u = slot_grid.row(align=True)
    r_u.scale_y = 1.55
    op_u = r_u.operator(
        "ezmod.set_gear_type",
        text="👕 КОСТЮМ / ФОРМА / ОДЕЖДА (Uniform)",
        depress=(props.gear_type == "UNIFORM"),
    )
    op_u.gear_type = "UNIFORM"

    r_s1 = slot_grid.row(align=True)
    r_s1.scale_y = 1.45
    op_h = r_s1.operator(
        "ezmod.set_gear_type",
        text="🪖 ШЛЕМ / КАСКА",
        depress=(props.gear_type == "HEADGEAR"),
    )
    op_h.gear_type = "HEADGEAR"
    op_v = r_s1.operator(
        "ezmod.set_gear_type",
        text="🦺 БРОНИК / ПЛИТНИК",
        depress=(props.gear_type == "VEST"),
    )
    op_v.gear_type = "VEST"

    r_s2 = slot_grid.row(align=True)
    r_s2.scale_y = 1.45
    op_b = r_s2.operator(
        "ezmod.set_gear_type",
        text="🎒 РЮКЗАК / СУМКА",
        depress=(props.gear_type == "BACKPACK"),
    )
    op_b.gear_type = "BACKPACK"
    op_g = r_s2.operator(
        "ezmod.set_gear_type",
        text="🥽 ОЧКИ / МАСКА",
        depress=(props.gear_type == "GOGGLES"),
    )
    op_g.gear_type = "GOGGLES"

    r_s3 = slot_grid.row(align=True)
    r_s3.scale_y = 1.45
    op_n = r_s3.operator(
        "ezmod.set_gear_type",
        text="🔦 ПНВ / ПОВЯЗКА",
        depress=(props.gear_type == "NVG"),
    )
    op_n.gear_type = "NVG"
    op_f = r_s3.operator(
        "ezmod.set_gear_type",
        text="🧑 ГОЛОВА / ЛИЦО",
        depress=(props.gear_type == "FACE"),
    )
    op_f.gear_type = "FACE"

    slot_drop = slot_box.row(align=True)
    slot_drop.scale_y = 1.3
    slot_drop.prop(props, "gear_type", text="Текущий слот")

    box2.prop(props, "item_name", text="Имя")
    box2.prop(props, "class_slug", text="ID")
    box2.prop(props, "author_name", text="Автор")

    row_stats = box2.row(align=True)
    if props.gear_type in ("HEADGEAR", "VEST"):
        row_stats.prop(props, "armor_val", text="Броня")
    if props.gear_type in ("VEST", "BACKPACK", "UNIFORM"):
        row_stats.prop(props, "load_val", text="Вмест.")
    row_stats.prop(props, "mass_val", text="Масса")

    col_tools = box2.column(align=True)
    col_tools.operator("ezmod.fix_multi_uv", icon="UV")
    col_tools.operator("ezmod.adapt_dxt5nm_normals", icon="IMAGE_DATA")
    if obj and obj.active_material:
        col_tools.operator(
            "ezmod.make_glass_node",
            text=f"🕶 Стекло Arma 3 ({obj.active_material.name[:22]})",
            icon="SHADING_RENDERED",
        )
    else:
        col_tools.operator("ezmod.make_glass_node", icon="SHADING_RENDERED")
    col_tools.operator("ezmod.auto_fit_gloves", icon="VIEW_PAN")
    col_tools.operator("ezmod.auto_rig", icon="MOD_VERTEX_WEIGHT")
    wp_text = "🚪 Выйти из Weight Paint" if context.mode == "PAINT_WEIGHT" else "🖌 Править веса кистью (Weight Paint)"
    col_tools.operator("ezmod.weight_paint_mode", text=wp_text, icon="BRUSH_DATA")

    adv_row = box2.row()
    adv_row.prop(
        props,
        "show_advanced_rig_tex",
        text="⚙ Доп. настройки (Текстуры / Кость)",
        icon="TRIA_DOWN" if props.show_advanced_rig_tex else "TRIA_RIGHT",
        emboss=False,
    )
    if props.show_advanced_rig_tex:
        adv_box = box2.box()
        adv_box.label(text="Ручной выбор текстур (если нет в материалах):", icon="TEXTURE")
        adv_box.prop(props, "diffuse_tex", text="Цвет (CO)")
        adv_box.prop(props, "normal_tex", text="Рельеф (NOHQ)")
        adv_box.prop(props, "rig_bone_mode", text="Кость")

    # --- Block 2.5: ACE Arsenal Extended (XtdGear / Bodygroups) ---
    box_ace = layout.box()
    box_ace.prop(props, "aceax_enabled", text="🧩 ACE Arsenal Варианты (Customizable)", icon="MOD_ARRAY")
    if props.aceax_enabled:
        box_ace.prop(props, "aceax_model", text="Группа ID")
        box_ace.prop(props, "aceax_label", text="Название")

        if len(props.aceax_options) == 0:
            row_av = box_ace.row(align=True)
            row_av.prop(props, "aceax_camo", text="Камо")
            row_av.prop(props, "aceax_variant", text="Вариант")
        else:
            for oi, opt in enumerate(props.aceax_options):
                sub_box = box_ace.box()
                hdr = sub_box.row(align=True)
                hdr.prop(opt, "option_id", text=f"ID {oi + 1}")
                op_add_v = hdr.operator("ezmod.aceax_add_value", text="", icon="ADD")
                op_add_v.opt_index = oi
                op_del_o = hdr.operator("ezmod.aceax_remove_option", text="", icon="REMOVE")
                op_del_o.opt_index = oi

                for vi, val_item in enumerate(opt.values):
                    vrow = sub_box.row(align=True)
                    is_sel = (vi == opt.active_value_idx) and opt.preview_enabled
                    op_sel = vrow.operator(
                        "ezmod.aceax_select_value",
                        text="",
                        icon="CHECKMARK" if is_sel else "RADIOBUT_OFF",
                        depress=is_sel,
                    )
                    op_sel.opt_index = oi
                    op_sel.val_index = vi
                    vrow.prop(val_item, "value", text=f"{vi + 1}")
                    has_snap = bool(val_item.scene_snapshot)
                    if not has_snap:
                        op_cap = vrow.operator(
                            "ezmod.aceax_capture_view",
                            text="Запомнить",
                            icon="FILE_TICK",
                        )
                        op_cap.opt_index = oi
                        op_cap.val_index = vi
                    else:
                        op_eye = vrow.operator(
                            "ezmod.aceax_select_value",
                            text="",
                            icon="HIDE_OFF" if is_sel else "HIDE_ON",
                            depress=is_sel,
                        )
                        op_eye.opt_index = oi
                        op_eye.val_index = vi
                        op_clr = vrow.operator("ezmod.aceax_clear_view", text="", icon="TRASH")
                        op_clr.opt_index = oi
                        op_clr.val_index = vi
                    if len(opt.values) > 1:
                        op_del_v = vrow.operator("ezmod.aceax_remove_value", text="", icon="REMOVE")
                        op_del_v.opt_index = oi
                        op_del_v.val_index = vi

        box_ace.operator("ezmod.aceax_add_option", text="+ Добавить ID категории", icon="ADD")

    # --- Block 2.7: Arma 3 LODs & Camera (1st Person) ---
    box_arma = layout.box()
    box_arma.label(text="🎖 Arma 3: LOD-ы и 1-е лицо:", icon="ARMATURE_DATA")
    row_arma = box_arma.row(align=True)
    row_arma.scale_y = 1.3
    row_arma.operator("ezmod.auto_generate_lods", text="⚡ Авто-Лодинг", icon="MOD_DECIM")

    has_empty_vp = bool(bpy.data.objects.get("LOD_View_Pilot_Empty"))
    row_arma.operator(
        "ezmod.toggle_first_person_hide",
        text="👁 Отключено в 1-м лице" if has_empty_vp else "👁 Включено в 1-м лице",
        icon="HIDE_ON" if has_empty_vp else "HIDE_OFF",
        depress=has_empty_vp,
    )

    # --- Block 3: One-Click Build & Mod Manager ---
    box3 = layout.box()
    box3.label(text="3. Папка мода (!Workshop) и Сборка", icon="FILE_FOLDER")
    row_mod = box3.row(align=True)
    row_mod.prop(props, "mod_folder", text="")
    row_mod.prop(props, "mod_folder_preset", text="", icon="DOWNARROW_HLT")
    row_mod.operator("ezmod.create_mod_folder", text="", icon="NEWFOLDER")

    col_build = box3.column(align=True)
    col_build.scale_y = 1.6
    col_build.operator("ezmod.build_mod", icon="EXPORT")

    if props.last_status:
        box3.label(text=props.last_status, icon="INFO")

    box3.operator("ezmod.open_mod_folder", icon="FILE_FOLDER")


class ATBX_PT_ez_mod_tools_panel(bpy.types.Panel):
    bl_idname = "ATBX_PT_ez_mod_tools_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Arma 3 Tools"
    bl_label = "⚡ EZ_MOD: Своя снаряга в 1 клик"

    def draw(self, context):
        draw_ez_mod_ui(self.layout, context)


class EZMOD_OT_sent_edit_to_weight_paint(bpy.types.Operator):
    bl_idname = "ezmod.sent_edit_to_weight_paint"
    bl_label = "🖌 Edit выделение → Weight Paint"
    bl_description = "Переносит выделенные в Edit Mode вершины прямо в Weight Paint (с маской выделения и активным скелетом для Ctrl+ЛКМ)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh)!")
            return {"CANCELLED"}

        has_sel = False
        if obj.mode == "EDIT":
            obj.update_from_editmode()
            has_sel = any(v.select for v in obj.data.vertices)
            bpy.ops.object.mode_set(mode="OBJECT")
        elif context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        if has_sel:
            obj.data.use_paint_mask_vertex = True
            obj.data.use_paint_mask = False

        arm = next((m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
        if arm is None:
            arm = bpy.data.objects.get("Arma3_Skeleton")
            if arm:
                bind_mesh_to_arma3_skeleton(obj, arm)

        bpy.ops.object.select_all(action="DESELECT")
        if arm:
            arm.hide_set(False)
            arm.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        context.tool_settings.use_auto_normalize = True

        self.report(
            {"INFO"},
            "Weight Paint с маской выделения! Ctrl+ЛКМ по кости, Shift+ЛКМ — сгладить."
            if has_sel
            else "Weight Paint готов! Ctrl+ЛКМ по кости для выбора.",
        )
        return {"FINISHED"}


class EZMOD_OT_sent_transfer_selected(bpy.types.Operator):
    bl_idname = "ezmod.sent_transfer_selected"
    bl_label = "⚡ Перенести на выделенное"
    bl_description = "Переносит веса с Source (или манекена) ТОЛЬКО на выделенные в Edit Mode / маске вершины, не трогая остальную модель"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh)!")
            return {"CANCELLED"}

        prev_mode = obj.mode
        if prev_mode == "EDIT":
            obj.update_from_editmode()
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        sel_indices = [v.index for v in obj.data.vertices if v.select]
        if not sel_indices:
            self.report({"WARNING"}, "Сначала выделите нужные вершины в Edit Mode (или маской)!")
            if prev_mode == "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")
            elif prev_mode == "WEIGHT_PAINT":
                bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
            return {"CANCELLED"}

        rwt = getattr(context.scene, "robust_weight_transfer_settings", None)
        src = rwt.source_object if (rwt and rwt.source_object and rwt.source_object != obj) else None
        if src is None:
            src = bpy.data.objects.get("Arma3_Mannequin")
        if src is None or src == obj or src.type != "MESH":
            self.report({"ERROR"}, "Выберите Source (донора) в поле Source выше или заспавните манекен!")
            return {"CANCELLED"}

        src_arm = next((m.object for m in src.modifiers if m.type == "ARMATURE" and m.object), None)
        mask_vg = obj.vertex_groups.new(name="__EZ_SEL_MASK")
        mask_vg.add(sel_indices, 1.0, "REPLACE")

        src_was_hidden = src.hide_get()
        src.hide_set(False)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj
        context.view_layer.update()

        try:
            dt_mod = obj.modifiers.new(name="SENT_SelTransfer", type="DATA_TRANSFER")
            dt_mod.object = src
            dt_mod.use_vert_data = True
            dt_mod.data_types_verts = {"VGROUP_WEIGHTS"}
            dt_mod.vert_mapping = "POLYINTERP_NEAREST"
            dt_mod.layers_vgroup_select_src = "ALL"
            dt_mod.layers_vgroup_select_dst = "NAME"
            dt_mod.vertex_group = mask_vg.name
            bpy.ops.object.datalayout_transfer(modifier=dt_mod.name)
            bpy.ops.object.modifier_apply(modifier=dt_mod.name)
            if src_arm:
                bind_mesh_to_arma3_skeleton(obj, src_arm)
        finally:
            if mask_vg.name in obj.vertex_groups:
                obj.vertex_groups.remove(obj.vertex_groups[mask_vg.name])
            src.hide_set(src_was_hidden)

        if prev_mode == "WEIGHT_PAINT":
            if src_arm:
                src_arm.select_set(True)
            bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        elif prev_mode == "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")

        self.report({"INFO"}, f"Перенесено с '{src.name}' на {len(sel_indices)} выд. вершин!")
        return {"FINISHED"}


class EZMOD_OT_sent_set_weight(bpy.types.Operator):
    bl_idname = "ezmod.sent_set_weight"
    bl_label = "Задать вес"
    bl_description = "Меняет вес кисти (или заливает выделенные вершины активной группы)"
    bl_options = {"REGISTER", "UNDO"}

    weight_val: bpy.props.FloatProperty(default=1.0)
    apply_fill: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        wp_ts = getattr(context.tool_settings, "weight_paint", None)
        ups = getattr(wp_ts, "unified_paint_settings", None) or getattr(context.tool_settings, "unified_paint_settings", None)
        if ups is not None and hasattr(ups, "weight"):
            ups.weight = self.weight_val
        wp_brush = getattr(wp_ts, "brush", None)
        if wp_brush is not None and hasattr(wp_brush, "weight"):
            wp_brush.weight = self.weight_val

        obj = _resolve_target_gear_mesh(context)
        if self.apply_fill and obj and obj.vertex_groups.active:
            if obj.mode == "WEIGHT_PAINT":
                bpy.ops.paint.weight_set()
            elif obj.mode == "EDIT":
                obj.update_from_editmode()
                sel = [v.index for v in obj.data.vertices if v.select]
                if sel:
                    bpy.ops.object.mode_set(mode="OBJECT")
                    obj.vertex_groups.active.add(sel, self.weight_val, "REPLACE")
                    bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class EZMOD_OT_sent_set_brush(bpy.types.Operator):
    bl_idname = "ezmod.sent_set_brush"
    bl_label = "Кисть Weight Paint"
    bl_description = "Переключает инструмент рисования весов (Рисование / Сглаживание / Усреднение)"

    brush_mode: bpy.props.StringProperty(default="DRAW")

    def execute(self, context):
        tool_map = {
            "DRAW": "builtin_brush.Draw",
            "BLUR": "builtin_brush.Blur",
            "AVERAGE": "builtin_brush.Average",
        }
        tid = tool_map.get(self.brush_mode, "builtin_brush.Draw")
        try:
            bpy.ops.wm.tool_set_by_id(name=tid)
        except Exception as exc:
            self.report({"WARNING"}, f"Не удалось переключить кисть: {exc}")
        return {"FINISHED"}


class EZMOD_OT_ps_brush_action(bpy.types.Operator):
    bl_idname = "ezmod.ps_brush_action"
    bl_label = "Photoshop Weight Action"
    bl_description = "Управление кистью весов в стиле Photoshop (Кисть, Ластик, Размытие, Жесткость)"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.StringProperty(default="BRUSH")
    float_val: bpy.props.FloatProperty(default=1.0)
    str_val: bpy.props.StringProperty(default="")

    def execute(self, context):
        ts = context.tool_settings
        wp = getattr(ts, "weight_paint", None)
        ups = getattr(wp, "unified_paint_settings", None) or getattr(ts, "unified_paint_settings", None)
        brush = getattr(wp, "brush", None) if wp else None

        if self.action == "BRUSH":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
            except Exception:
                pass
            w = self.float_val if self.float_val > 0.0 else 1.0
            if ups and hasattr(ups, "weight"):
                ups.weight = w
            if brush:
                if hasattr(brush, "weight"):
                    brush.weight = w
                if hasattr(brush, "blend"):
                    brush.blend = 'MIX'
            self.report({'INFO'}, f"🎨 Кисть (Вес: {w:.2f})")

        elif self.action == "ERASER":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
            except Exception:
                pass
            if ups and hasattr(ups, "weight"):
                ups.weight = 0.0
            if brush:
                if hasattr(brush, "weight"):
                    brush.weight = 0.0
                if hasattr(brush, "blend"):
                    brush.blend = 'MIX'
            self.report({'INFO'}, "🧹 Ластик (Вес 0.0 — стирает влияние кости)")

        elif self.action == "BLUR":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Blur")
            except Exception:
                pass
            self.report({'INFO'}, "💧 Размытие / Сглаживание (Blur)")

        elif self.action == "AVERAGE":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Average")
            except Exception:
                pass
            self.report({'INFO'}, "⚖ Усреднение (Average)")

        elif self.action == "TOGGLE_X":
            cur_w = ups.weight if (ups and hasattr(ups, "weight")) else (brush.weight if brush else 1.0)
            new_w = 0.0 if cur_w > 0.01 else 1.0
            if ups and hasattr(ups, "weight"):
                ups.weight = new_w
            if brush and hasattr(brush, "weight"):
                brush.weight = new_w
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
            except Exception:
                pass
            status = "🧹 Ластик (0.0)" if new_w == 0.0 else "🎨 Кисть (1.0)"
            self.report({'INFO'}, f"Смена цвета [X]: {status}")

        elif self.action == "SET_WEIGHT":
            if ups and hasattr(ups, "weight"):
                ups.weight = self.float_val
            if brush and hasattr(brush, "weight"):
                brush.weight = self.float_val
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
            except Exception:
                pass
            self.report({'INFO'}, f"Вес кисти: {self.float_val:.2f}")

        elif self.action == "SET_HARDNESS":
            preset = self.str_val or "SMOOTH"
            if brush and hasattr(brush, "curve_distance_falloff_preset"):
                brush.curve_distance_falloff_preset = preset
            lbl = "Мягкая" if preset == "SMOOTH" else ("Жёсткая" if preset == "CONSTANT" else "Средняя")
            self.report({'INFO'}, f"Жёсткость кисти: {lbl}")

        elif self.action == "SIZE_STEP":
            step = int(self.float_val)
            cur_sz = ups.size if (ups and hasattr(ups, "size")) else (brush.size if brush else 50)
            new_sz = max(1, min(1000, cur_sz + step))
            if ups and hasattr(ups, "size"):
                ups.size = new_sz
            if brush and hasattr(brush, "size"):
                brush.size = new_sz
            self.report({'INFO'}, f"Размер кисти: {new_sz} px")

        elif self.action == "TOGGLE_FRONTFACE":
            if brush and hasattr(brush, "use_frontface"):
                brush.use_frontface = not brush.use_frontface
                state = "ВКЛ (не красит насквозь)" if brush.use_frontface else "ВЫКЛ (красит насквозь)"
                self.report({'INFO'}, f"Только видимые грани: {state}")

        elif self.action == "TOGGLE_AUTONORM":
            if ts:
                ts.use_auto_normalize = not ts.use_auto_normalize
                state = "ВКЛ" if ts.use_auto_normalize else "ВЫКЛ"
                self.report({'INFO'}, f"Авто-нормализация: {state}")

        elif self.action == "QUICK_FILL":
            cur_w = ups.weight if (ups and hasattr(ups, "weight")) else (brush.weight if brush else 1.0)
            obj = _resolve_target_gear_mesh(context)
            if not obj or not obj.vertex_groups.active:
                self.report({'WARNING'}, "Нет активной группы вершин / кости!")
                return {'CANCELLED'}
            vg = obj.vertex_groups.active
            use_mask = bool(getattr(obj.data, "use_paint_mask_vertex", False))
            prev_mode = obj.mode
            if prev_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            try:
                if use_mask:
                    verts = [v.index for v in obj.data.vertices if v.select]
                    if not verts:
                        verts = [v.index for v in obj.data.vertices]
                else:
                    verts = [v.index for v in obj.data.vertices]
                vg.add(verts, cur_w, 'REPLACE')
            finally:
                if prev_mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode=prev_mode)
            if ts and ts.use_auto_normalize:
                try:
                    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=True)
                except Exception:
                    pass
            self.report({'INFO'}, f"🪣 Залит вес {cur_w:.2f} в группу '{vg.name}' ({len(verts)} верш.)")

        return {"FINISHED"}


class EZMOD_OT_auto_generate_lods(bpy.types.Operator):
    bl_idname = "ezmod.auto_generate_lods"
    bl_label = "⚡ Создать LOD-ы (Авто-лодинг)"
    bl_description = "Создаёт оптимизированные лоды для Arma 3 (LOD 2: 50%, LOD 3: 25%, LOD 4: 10% + Тень + Geometry) с правильными свойствами ArmaToolbox"
    bl_options = {"REGISTER", "UNDO"}

    create_shadow: bpy.props.BoolProperty(name="Создать Shadow Volume", default=True)
    create_geometry: bpy.props.BoolProperty(name="Создать Geometry (коллизия)", default=True)

    def execute(self, context):
        src_obj = _resolve_target_gear_mesh(context)
        if not src_obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh) для генерации LOD-ов!")
            return {"CANCELLED"}

        # 1. Ensure src_obj is Resolution 1.000
        src_obj.name = src_obj.name.split("_LOD")[0]
        if hasattr(src_obj, "armaObjProps"):
            src_obj.armaObjProps.isArmaObject = True
            src_obj.armaObjProps.lod = "-1.0"
            src_obj.armaObjProps.lodDistance = 0.0

        coll_name = f"LODs_{src_obj.name}"
        coll = bpy.data.collections.get(coll_name)
        if not coll:
            coll = bpy.data.collections.new(coll_name)
            (context.collection or context.scene.collection).children.link(coll)

        lod_specs = [
            (2, 0.50, 6.0),
            (3, 0.25, 18.0),
            (4, 0.10, 40.0),
        ]

        created_objs = []
        for l_idx, ratio, dist in lod_specs:
            lod_obj_name = f"{src_obj.name}_LOD{l_idx}"
            old_obj = bpy.data.objects.get(lod_obj_name)
            if old_obj:
                bpy.data.objects.remove(old_obj, do_unlink=True)

            lod_mesh = src_obj.data.copy()
            lod_mesh.name = f"{src_obj.data.name}_LOD{l_idx}"
            lod_obj = bpy.data.objects.new(lod_obj_name, lod_mesh)
            lod_obj.matrix_world = src_obj.matrix_world.copy()
            coll.objects.link(lod_obj)

            # Copy modifiers (especially Armature for rigged gear)
            for m in src_obj.modifiers:
                if m.type == 'ARMATURE':
                    new_arm = lod_obj.modifiers.new(name=m.name, type='ARMATURE')
                    new_arm.object = m.object

            # Add decimate modifier so user can adjust it interactively
            dec_mod = lod_obj.modifiers.new(name="Decimate", type='DECIMATE')
            dec_mod.ratio = ratio

            if hasattr(lod_obj, "armaObjProps"):
                lod_obj.armaObjProps.isArmaObject = True
                lod_obj.armaObjProps.lod = "-1.0"
                lod_obj.armaObjProps.lodDistance = dist

            created_objs.append(lod_obj_name)

        if self.create_shadow:
            sh_name = f"{src_obj.name}_Shadow"
            old_sh = bpy.data.objects.get(sh_name)
            if old_sh:
                bpy.data.objects.remove(old_sh, do_unlink=True)
            sh_mesh = src_obj.data.copy()
            sh_obj = bpy.data.objects.new(sh_name, sh_mesh)
            sh_obj.matrix_world = src_obj.matrix_world.copy()
            coll.objects.link(sh_obj)
            for m in src_obj.modifiers:
                if m.type == 'ARMATURE':
                    new_arm = sh_obj.modifiers.new(name=m.name, type='ARMATURE')
                    new_arm.object = m.object
            dec_mod = sh_obj.modifiers.new(name="Decimate", type='DECIMATE')
            dec_mod.ratio = 0.20
            if hasattr(sh_obj, "armaObjProps"):
                sh_obj.armaObjProps.isArmaObject = True
                sh_obj.armaObjProps.lod = "1.100e+4"  # Shadow Volume
            created_objs.append(sh_name)

        if self.create_geometry:
            geo_name = f"{src_obj.name}_Geometry"
            old_geo = bpy.data.objects.get(geo_name)
            if old_geo:
                bpy.data.objects.remove(old_geo, do_unlink=True)
            geo_mesh = src_obj.data.copy()
            geo_obj = bpy.data.objects.new(geo_name, geo_mesh)
            geo_obj.matrix_world = src_obj.matrix_world.copy()
            coll.objects.link(geo_obj)
            dec_mod = geo_obj.modifiers.new(name="Decimate", type='DECIMATE')
            dec_mod.ratio = 0.08
            if hasattr(geo_obj, "armaObjProps"):
                geo_obj.armaObjProps.isArmaObject = True
                geo_obj.armaObjProps.lod = "1.000e+13"  # Geometry
            created_objs.append(geo_name)

        self.report({"INFO"}, f"⚡ Созданы LOD-ы ({', '.join(created_objs)}) в коллекции '{coll_name}'")
        return {"FINISHED"}


class EZMOD_OT_toggle_first_person_hide(bpy.types.Operator):
    bl_idname = "ezmod.toggle_first_person_hide"
    bl_label = "👁 Скрыть/Показать от 1-го лица"
    bl_description = "Создаёт или удаляет пустой лод View Pilot (1100). В Arma 3 пустой View Pilot скрывает очки/шлем/снаряжение из глаз игрока от 1-го лица"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pilot_name = "LOD_View_Pilot_Empty"
        existing = bpy.data.objects.get(pilot_name)
        props = getattr(context.scene, "ez_mod_props", None)

        if existing:
            bpy.data.objects.remove(existing, do_unlink=True)
            if props:
                props.hide_in_first_person = False
            self.report({"INFO"}, "👁 Ассет ВКЛЮЧЕН в 1-м лице (пустой View Pilot удален)")
        else:
            coll = context.collection or bpy.context.scene.collection
            mesh = bpy.data.meshes.new(pilot_name)
            obj = bpy.data.objects.new(pilot_name, mesh)
            coll.objects.link(obj)
            if hasattr(obj, "armaObjProps"):
                obj.armaObjProps.isArmaObject = True
                obj.armaObjProps.lod = "1.100e+3"  # View Pilot
            if props:
                props.hide_in_first_person = True
            self.report({"INFO"}, "👁 Ассет ОТКЛЮЧЕН от 1-го лица! В игре очки/шлем не перекрывают обзор камеры")

        return {"FINISHED"}


class EZMOD_OT_arma_setup_first_person_hide(bpy.types.Operator):
    bl_idname = "ezmod.arma_setup_first_person_hide"
    bl_label = "👁 Скрыть очки/шлем от 1-го лица"
    bl_description = "Создаёт пустой лод View Pilot (1100), чтобы очки, маска или козырёк шлема не лезли перед глазами в виде от 1-го лица в игре"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return bpy.ops.ezmod.toggle_first_person_hide()


class EZMOD_OT_sent_smart_transfer(bpy.types.Operator):
    bl_idname = "ezmod.sent_smart_transfer"
    bl_label = "⚡ Умный перенос (откуда угодно)"
    bl_description = "Переносит веса с выбранного в SENT объекта-донора (или с Arma3_Mannequin), не падает на отдельных деталях/подсумках, сам вешает Armature и сглаживает"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh), на которую переносить веса!")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        rwt = getattr(context.scene, "robust_weight_transfer_settings", None)
        src = rwt.source_object if (rwt and rwt.source_object and rwt.source_object != obj) else None
        if src is None:
            src = bpy.data.objects.get("Arma3_Mannequin")
        if src is None or src == obj or src.type != "MESH":
            self.report({"ERROR"}, "Выберите Source (донора) в поле Source выше или заспавните манекен!")
            return {"CANCELLED"}

        # Find source armature (if any) and temporarily reset pose
        src_arm = None
        for m in src.modifiers:
            if m.type == "ARMATURE" and m.object:
                src_arm = m.object
                break
        if src_arm is None and src.name == "Arma3_Mannequin":
            src_arm = ensure_arma3_skeleton(context)
            bind_mesh_to_arma3_skeleton(src, src_arm)

        saved_pose = {}
        if src_arm and src_arm.pose:
            for pb in src_arm.pose.bones:
                saved_pose[pb.name] = (
                    pb.rotation_mode,
                    tuple(pb.rotation_euler),
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                )
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.location = (0.0, 0.0, 0.0)

        src_was_hidden = src.hide_get()
        src.hide_set(False)
        context.view_layer.update()

        method_used = "Data Transfer"
        try:
            # 1) Try Robust Weight Transfer with tolerant settings first
            rwt_ok = False
            if rwt is not None and hasattr(bpy.ops.object, "skin_weight_transfer"):
                try:
                    rwt.source_object = src
                    rwt.apply_to_selected = False
                    if hasattr(rwt, "group_selection"):
                        rwt.group_selection = "DEFORM_POSE_BONES" if src_arm else "ALL_GROUPS"
                    if hasattr(rwt, "max_distance"):
                        rwt.max_distance = max(rwt.max_distance, 0.25)
                    if hasattr(rwt, "max_normal_angle_difference"):
                        import math
                        rwt.max_normal_angle_difference = math.pi
                    context.view_layer.update()
                    if bpy.ops.object.skin_weight_transfer.poll():
                        res = bpy.ops.object.skin_weight_transfer()
                        if "FINISHED" in res:
                            rwt_ok = True
                            method_used = "Robust Weight Transfer"
                except Exception:
                    rwt_ok = False

            # 2) Reliable fallback: Blender Data Transfer (never fails on loose parts)
            if not rwt_ok:
                dt_mod = obj.modifiers.new(name="SENT_DataTransfer", type="DATA_TRANSFER")
                dt_mod.object = src
                dt_mod.use_vert_data = True
                dt_mod.data_types_verts = {"VGROUP_WEIGHTS"}
                dt_mod.vert_mapping = "POLYINTERP_NEAREST"
                dt_mod.layers_vgroup_select_src = "ALL"
                dt_mod.layers_vgroup_select_dst = "NAME"
                bpy.ops.object.datalayout_transfer(modifier=dt_mod.name)
                bpy.ops.object.modifier_apply(modifier=dt_mod.name)

            if src_arm:
                bind_mesh_to_arma3_skeleton(obj, src_arm)

            if len(obj.vertex_groups) > 0:
                sel_mode = "BONE_DEFORM" if src_arm else "ALL"
                obj.vertex_groups.active_index = 0
                bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                bpy.ops.object.vertex_group_smooth(
                    group_select_mode=sel_mode,
                    factor=0.35,
                    repeat=2,
                    expand=0.0,
                )
                bpy.ops.object.vertex_group_limit_total(
                    group_select_mode=sel_mode,
                    limit=4,
                )
                bpy.ops.object.vertex_group_normalize_all(
                    group_select_mode=sel_mode,
                    lock_active=False,
                )
                bpy.ops.object.mode_set(mode="OBJECT")
        finally:
            src.hide_set(src_was_hidden)
            if obj.mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass
            if src_arm and src_arm.pose and saved_pose:
                for pb in src_arm.pose.bones:
                    if pb.name in saved_pose:
                        rmode, reul, rquat, rloc = saved_pose[pb.name]
                        pb.rotation_mode = rmode
                        pb.rotation_euler = reul
                        pb.rotation_quaternion = rquat
                        pb.location = rloc

        self.report({"INFO"}, f"Веса перенесены с '{src.name}' ({method_used})!")
        return {"FINISHED"}


class EZMOD_OT_sent_rigid_to_bone(bpy.types.Operator):
    bl_idname = "ezmod.sent_rigid_to_bone"
    bl_label = "🎯 Привязать жёстко к кости"
    bl_description = "Назначает вес 1.0 выбранной кости на весь объект (или только на выделенные вершины в Edit Mode), убирая влияние других костей"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh)!")
            return {"CANCELLED"}
        props = context.scene.ez_mod_props
        bname = props.sent_target_bone
        if not bname:
            self.report({"ERROR"}, "Выберите кость!")
            return {"CANCELLED"}

        was_edit = (obj.mode == "EDIT")
        if was_edit:
            obj.update_from_editmode()
            bpy.ops.object.mode_set(mode="OBJECT")
            v_indices = [v.index for v in obj.data.vertices if v.select]
            if not v_indices:
                v_indices = [v.index for v in obj.data.vertices]
        else:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            v_indices = [v.index for v in obj.data.vertices]

        # Ensure Armature modifier exists if Arma3_Skeleton is present
        has_arm = any(m.type == "ARMATURE" and m.object for m in obj.modifiers)
        if not has_arm:
            arm = bpy.data.objects.get("Arma3_Skeleton")
            if arm:
                bind_mesh_to_arma3_skeleton(obj, arm)

        # Remove deform weights on target vertices, keeping non-bone groups like 'camo'
        arm_obj = next((m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
        arm_bones = {b.name for b in arm_obj.data.bones} if (arm_obj and arm_obj.data) else set()
        for vg in obj.vertex_groups:
            if vg.name == bname:
                continue
            if (vg.name in arm_bones) or (vg.name.lower() in ARMA3_BONE_NAMES_SET):
                vg.remove(v_indices)

        vg_target = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)
        vg_target.add(v_indices, 1.0, "REPLACE")

        if was_edit:
            bpy.ops.object.mode_set(mode="EDIT")

        self.report({"INFO"}, f"Привязано жёстко к '{bname}' ({len(v_indices)} верш.)")
        return {"FINISHED"}


class EZMOD_OT_sent_mirror_weights(bpy.types.Operator):
    bl_idname = "ezmod.sent_mirror_weights"
    bl_label = "🪞 Отзеркалить (L ↔ R)"
    bl_description = "Зеркалит веса левых/правых костей (.L/.R, _L/_R, Left/Right)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj or len(obj.vertex_groups) == 0:
            self.report({"WARNING"}, "У модели нет весов для зеркалирования!")
            return {"CANCELLED"}
        prev_mode = obj.mode
        if context.mode != "OBJECT" and context.active_object != obj:
            bpy.ops.object.mode_set(mode="OBJECT")
        if context.active_object != obj:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj
        try:
            bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
            bpy.ops.object.vertex_group_mirror(mirror_weights=True, flip_group_names=True, all_groups=True)
        except Exception as exc:
            self.report({"WARNING"}, f"Ошибка зеркала: {exc}")
            return {"CANCELLED"}
        finally:
            if prev_mode != "WEIGHT_PAINT" and obj.mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass
        self.report({"INFO"}, "Веса отзеркалены (L ↔ R)!")
        return {"FINISHED"}


class EZMOD_OT_sent_clean_empty_vgroups(bpy.types.Operator):
    bl_idname = "ezmod.sent_clean_empty_vgroups"
    bl_label = "🧹 Удалить пустые группы"
    bl_description = "Удаляет все группы вершин (Vertex Groups), у которых нет ненулевых весов"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Выделите 3D-модель (Mesh)!")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        used = set()
        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0005:
                    used.add(g.group)
        removed = 0
        for vg in list(obj.vertex_groups):
            if vg.index not in used:
                obj.vertex_groups.remove(vg)
                removed += 1
        self.report({"INFO"}, f"Удалено пустых групп: {removed}")
        return {"FINISHED"}


class EZMOD_OT_transfer_from_mannequin(bpy.types.Operator):
    bl_idname = "ezmod.transfer_from_mannequin"
    bl_label = "🧲 1. Перенести веса с манекена"
    bl_description = "Переносит веса напрямую с поверхности Arma3_Mannequin (работает и на отдельных подсумках/карманах без ошибок), сглаживает и привязывает к скелету"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Не найдена 3D-модель снаряжения (Mesh)!")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        arm = ensure_arma3_skeleton(context)
        mannequin = bpy.data.objects.get("Arma3_Mannequin")
        if mannequin is None:
            try:
                bpy.ops.ezmod.spawn_mannequin()
                mannequin = bpy.data.objects.get("Arma3_Mannequin")
            except Exception:
                mannequin = None
        if mannequin is None or mannequin.type != "MESH":
            self.report({"ERROR"}, "Не найден меш Arma3_Mannequin!")
            return {"CANCELLED"}

        # Save & temporarily reset pose so surface transfer happens in rest T-pose
        saved_pose = {}
        if arm and arm.pose:
            for pb in arm.pose.bones:
                saved_pose[pb.name] = (
                    pb.rotation_mode,
                    tuple(pb.rotation_euler),
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                )
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.location = (0.0, 0.0, 0.0)

        disabled_mods = []
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.show_viewport:
                m.show_viewport = False
                disabled_mods.append(m)

        man_was_hidden = mannequin.hide_get()
        mannequin.hide_set(False)
        context.view_layer.update()

        try:
            for vg in list(obj.vertex_groups):
                if vg.name.lower() in ARMA3_BONE_NAMES_SET or vg.name.lower().startswith("camo"):
                    obj.vertex_groups.remove(vg)

            # Ensure mannequin has bone groups; if empty, load from MANNEQUIN_PATH
            has_man_bones = any(vg.name.lower() in ARMA3_BONE_NAMES_SET for vg in mannequin.vertex_groups)
            if not has_man_bones and os.path.exists(MANNEQUIN_PATH):
                _, _, _, m_sels = load_mlod_lod0_data(MANNEQUIN_PATH)
                for bname, bmap in m_sels.items():
                    if bname.lower() in ARMA3_BONE_NAMES_SET:
                        mvg = mannequin.vertex_groups.get(bname) or mannequin.vertex_groups.new(name=bname)
                        for m_vi, mw_val in bmap.items():
                            if m_vi < len(mannequin.data.vertices):
                                mvg.add([m_vi], mw_val, "REPLACE")

            _ensure_mannequin_finger_weights(mannequin)

            # Use Blender's surface-interpolated Data Transfer (never fails on loose parts)
            dt_mod = obj.modifiers.new(name="EZ_DataTransfer", type="DATA_TRANSFER")
            dt_mod.object = mannequin
            dt_mod.use_vert_data = True
            dt_mod.data_types_verts = {"VGROUP_WEIGHTS"}
            dt_mod.vert_mapping = "POLYINTERP_NEAREST"
            dt_mod.layers_vgroup_select_src = "ALL"
            dt_mod.layers_vgroup_select_dst = "NAME"

            bpy.ops.object.datalayout_transfer(modifier=dt_mod.name)
            bpy.ops.object.modifier_apply(modifier=dt_mod.name)

            # Remove non-bone groups that may have come from mannequin except bone groups
            for vg in list(obj.vertex_groups):
                if vg.name.lower() not in ARMA3_BONE_NAMES_SET:
                    obj.vertex_groups.remove(vg)

            for m in disabled_mods:
                m.show_viewport = True

            bind_mesh_to_arma3_skeleton(obj, arm)

            if len(obj.vertex_groups) > 0:
                obj.vertex_groups.active_index = 0
                bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                bpy.ops.object.vertex_group_smooth(
                    group_select_mode="BONE_DEFORM",
                    factor=0.45,
                    repeat=3,
                    expand=0.0,
                )
                bpy.ops.object.vertex_group_limit_total(
                    group_select_mode="BONE_DEFORM",
                    limit=4,
                )
                bpy.ops.object.vertex_group_normalize_all(
                    group_select_mode="BONE_DEFORM",
                    lock_active=False,
                )
                bpy.ops.object.mode_set(mode="OBJECT")

            vg_camo = obj.vertex_groups.get("camo") or obj.vertex_groups.new(name="camo")
            if len(obj.data.vertices) > 0:
                vg_camo.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        finally:
            mannequin.hide_set(man_was_hidden)
            for m in disabled_mods:
                m.show_viewport = True
            if obj.mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass
            if arm and arm.pose and saved_pose:
                for pb in arm.pose.bones:
                    if pb.name in saved_pose:
                        rmode, reul, rquat, rloc = saved_pose[pb.name]
                        pb.rotation_mode = rmode
                        pb.rotation_euler = reul
                        pb.rotation_quaternion = rquat
                        pb.location = rloc

        if hasattr(obj, "armaObjProps"):
            obj.armaObjProps.isArmaObject = True

        bones_list = ", ".join(
            vg.name for vg in obj.vertex_groups if vg.name.lower() in ARMA3_BONE_NAMES_SET
        )
        self.report({"INFO"}, f"Веса перенесены с манекена! Кости: {bones_list}")
        return {"FINISHED"}


class EZMOD_OT_auto_rig_vest_rigid(bpy.types.Operator):
    bl_idname = "ezmod.auto_rig_vest_rigid"
    bl_label = "🛡 Плитник жёстко (spine3 + spine2)"
    bl_description = "Привязывает плитник/броник жёстко к грудной клетке (spine3 + плавный низ на spine2) без вытягивания руками и плечами"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Не найдена 3D-модель снаряжения (Mesh)!")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        arm = ensure_arma3_skeleton(context)

        # Save & temporarily reset pose to get clean rest-pose coordinates
        saved_pose = {}
        if arm and arm.pose:
            for pb in arm.pose.bones:
                saved_pose[pb.name] = (
                    pb.rotation_mode,
                    tuple(pb.rotation_euler),
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                )
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.location = (0.0, 0.0, 0.0)

        disabled_mods = []
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.show_viewport:
                m.show_viewport = False
                disabled_mods.append(m)
        context.view_layer.update()

        # Find spine2 / spine3 Z bounds from Arma3_Skeleton rest bones
        z_spine2 = 1.00
        z_spine3 = 1.18
        if arm and arm.data:
            b2 = arm.data.bones.get("spine2")
            b3 = arm.data.bones.get("spine3")
            if b2:
                z_spine2 = (arm.matrix_world @ b2.head_local).z
            if b3:
                z_spine3 = (arm.matrix_world @ b3.head_local).z
        if z_spine3 <= z_spine2 + 0.02:
            z_spine3 = z_spine2 + 0.15

        for vg in list(obj.vertex_groups):
            if vg.name.lower() in ARMA3_BONE_NAMES_SET or vg.name.lower().startswith("camo"):
                obj.vertex_groups.remove(vg)

        vg_s3 = obj.vertex_groups.new(name="spine3")
        vg_s2 = obj.vertex_groups.new(name="spine2")
        vg_camo = obj.vertex_groups.new(name="camo")

        mw = obj.matrix_world
        all_idx = []
        for vi, v in enumerate(obj.data.vertices):
            all_idx.append(vi)
            wz = (mw @ v.co).z
            if wz >= z_spine3:
                w3, w2 = 1.0, 0.0
            elif wz <= z_spine2:
                w3, w2 = 0.25, 0.75
            else:
                t = (wz - z_spine2) / (z_spine3 - z_spine2)
                w3 = 0.25 + 0.75 * t
                w2 = 1.0 - w3
            vg_s3.add([vi], w3, "REPLACE")
            if w2 > 0.001:
                vg_s2.add([vi], w2, "REPLACE")
        if all_idx:
            vg_camo.add(all_idx, 1.0, "REPLACE")

        for m in disabled_mods:
            m.show_viewport = True

        bind_mesh_to_arma3_skeleton(obj, arm)

        if arm and arm.pose and saved_pose:
            for pb in arm.pose.bones:
                if pb.name in saved_pose:
                    rmode, reul, rquat, rloc = saved_pose[pb.name]
                    pb.rotation_mode = rmode
                    pb.rotation_euler = reul
                    pb.rotation_quaternion = rquat
                    pb.location = rloc

        if hasattr(obj, "armaObjProps"):
            obj.armaObjProps.isArmaObject = True

        self.report({"INFO"}, "Плитник привязан жёстко (spine3 + spine2, без плеч)!")
        return {"FINISHED"}


class EZMOD_OT_smooth_weights(bpy.types.Operator):
    bl_idname = "ezmod.smooth_weights"
    bl_label = "✨ 2. Сгладить веса (Smooth)"
    bl_description = "Сглаживает веса костей, ограничивает до 4 костей на вершину (стандарт Arma 3) и нормализует"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _resolve_target_gear_mesh(context)
        if not obj:
            self.report({"ERROR"}, "Не найдена 3D-модель снаряжения (Mesh)!")
            return {"CANCELLED"}
        if context.mode != "OBJECT" and context.active_object != obj:
            bpy.ops.object.mode_set(mode="OBJECT")
        if context.active_object != obj:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj
        prev_mode = obj.mode
        if len(obj.vertex_groups) == 0:
            self.report({"WARNING"}, "У модели нет групп вершин (сначала нажми Перенести веса / Плитник)!")
            return {"CANCELLED"}
        try:
            if obj.vertex_groups.active_index < 0:
                obj.vertex_groups.active_index = 0
            if obj.mode != "WEIGHT_PAINT":
                bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
            bpy.ops.object.vertex_group_smooth(
                group_select_mode="BONE_DEFORM",
                factor=0.5,
                repeat=3,
                expand=0.0,
            )
            bpy.ops.object.vertex_group_limit_total(
                group_select_mode="BONE_DEFORM",
                limit=4,
            )
            bpy.ops.object.vertex_group_normalize_all(
                group_select_mode="BONE_DEFORM",
                lock_active=False,
            )
            if prev_mode != "WEIGHT_PAINT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as exc:
            if obj.mode != "OBJECT" and prev_mode != "WEIGHT_PAINT":
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass
            self.report({"WARNING"}, f"Не удалось сгладить веса: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Веса сглажены и нормализованы (макс. 4 кости на вершину)!")
        return {"FINISHED"}


def draw_ps_brush_context_menu(self, context):
    if context.mode != "PAINT_WEIGHT":
        return
    layout = self.layout
    ts = context.tool_settings
    wp = getattr(ts, "weight_paint", None)
    ups = getattr(wp, "unified_paint_settings", None) or getattr(ts, "unified_paint_settings", None)
    brush = getattr(wp, "brush", None) if wp else None
    obj = context.object

    box = layout.box()
    box.label(text="🎨 Photoshop Weight Brush (ПКМ Меню):", icon="BRUSH_DATA")

    # Режимы (B / E / R / G)
    row_tools = box.row(align=True)
    row_tools.scale_y = 1.3
    op = row_tools.operator("ezmod.ps_brush_action", text="🎨 Кисть (B)")
    op.action = "BRUSH"
    op = row_tools.operator("ezmod.ps_brush_action", text="🧹 Ластик (E)")
    op.action = "ERASER"
    op = row_tools.operator("ezmod.ps_brush_action", text="💧 Размыть (R)")
    op.action = "BLUR"
    op = row_tools.operator("ezmod.ps_brush_action", text="🪣 Залить")
    op.action = "QUICK_FILL"

    # Ползунки: Размер и Сила
    col = box.column(align=True)
    if ups and hasattr(ups, "size"):
        col.prop(ups, "size", text="Размер (Size)")
    elif brush and hasattr(brush, "size"):
        col.prop(brush, "size", text="Размер (Size)")
    if brush and hasattr(brush, "strength"):
        col.prop(brush, "strength", text="Сила (Opacity)", slider=True)

    # Жёсткость
    row_h = box.row(align=True)
    row_h.label(text="Жёсткость:")
    cur_falloff = getattr(brush, "curve_distance_falloff_preset", "SMOOTH") if brush else "SMOOTH"
    op1 = row_h.operator("ezmod.ps_brush_action", text="Мягкая", depress=(cur_falloff == "SMOOTH"))
    op1.action = "SET_HARDNESS"
    op1.str_val = "SMOOTH"
    op2 = row_h.operator("ezmod.ps_brush_action", text="Средняя", depress=(cur_falloff in ("SHARP", "SPHERE")))
    op2.action = "SET_HARDNESS"
    op2.str_val = "SHARP"
    op3 = row_h.operator("ezmod.ps_brush_action", text="Жёсткая", depress=(cur_falloff == "CONSTANT"))
    op3.action = "SET_HARDNESS"
    op3.str_val = "CONSTANT"

    # Быстрый вес (Палитра)
    row_w = box.row(align=True)
    cur_w = ups.weight if (ups and hasattr(ups, "weight")) else (brush.weight if brush else 1.0)
    for w_val, w_txt in [(1.0, "100%"), (0.75, "75%"), (0.5, "50%"), (0.25, "25%"), (0.0, "0% Ластик")]:
        op_w = row_w.operator("ezmod.ps_brush_action", text=w_txt, depress=(abs(cur_w - w_val) < 0.05))
        op_w.action = "SET_WEIGHT"
        op_w.float_val = w_val

    # Активная кость
    if obj and obj.type == "MESH":
        vg = obj.vertex_groups.active
        b_box = box.box()
        row_bone = b_box.row()
        row_bone.label(text=f"Кость: {vg.name if vg else 'Не выбрана'}", icon="BONE_DATA")
        row_bone.label(text="(Ctrl+ЛКМ = клик по кости)", icon="INFO")

    # Защита от брака
    row_safe = box.row(align=True)
    if brush and hasattr(brush, "use_frontface"):
        row_safe.prop(brush, "use_frontface", text="Не насквозь", toggle=True, icon="HIDE_OFF")
    if ts:
        row_safe.prop(ts, "use_auto_normalize", text="Авто-норм.", toggle=True, icon="NORMALIZE_FCURVES")
    if obj and hasattr(obj.data, "use_mirror_x"):
        row_safe.prop(obj.data, "use_mirror_x", text="X", toggle=True)

    row_arma_m = box.row(align=True)
    row_arma_m.operator("ezmod.auto_generate_lods", text="⚡ Авто-Лодинг", icon="MOD_DECIM")
    has_empty_vp = bool(bpy.data.objects.get("LOD_View_Pilot_Empty"))
    row_arma_m.operator(
        "ezmod.toggle_first_person_hide",
        text="👁 1-е лицо: Скрыто" if has_empty_vp else "👁 1-е лицо: Видно",
        icon="HIDE_ON" if has_empty_vp else "HIDE_OFF",
        depress=has_empty_vp,
    )

    box.separator()


class ATBX_PT_ez_sent_lazy_panel(bpy.types.Panel):
    bl_idname = "ATBX_PT_ez_sent_lazy_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SENT"
    bl_order = 0
    bl_label = "⚡ SENT+: Умные веса для лентяев"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ez_mod_props
        obj = _resolve_target_gear_mesh(context)

        # --- Quick Mode & Edit Selection Transfer ---
        box_mode = layout.box()
        col_m = box_mode.column(align=True)
        col_m.scale_y = 1.25
        if context.mode == "PAINT_WEIGHT":
            col_m.operator("ezmod.weight_paint_mode", text="🚪 Выйти из Weight Paint", icon="OBJECT_DATAMODE")
        else:
            col_m.operator("ezmod.sent_edit_to_weight_paint", text="🖌 Выделенное → Weight Paint", icon="BRUSH_DATA")

        if obj and obj.type == "MESH" and context.mode == "PAINT_WEIGHT":
            row_mask = box_mode.row(align=True)
            row_mask.prop(obj.data, "use_paint_mask_vertex", text="🎭 Маска вершин", toggle=True)
            row_mask.prop(context.tool_settings, "use_auto_normalize", text="Авто-норм.", toggle=True)
            row_mask.prop(obj.data, "use_mirror_vertex_groups", text="X", toggle=True)

        # --- Fast Transfer ---
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("ezmod.sent_smart_transfer", text="⚡ Перенести с Source (весь)", icon="MOD_DATA_TRANSFER")
        col.operator("ezmod.sent_transfer_selected", text="✂ Перенести ТОЛЬКО на выделенное", icon="RESTRICT_SELECT_OFF")
        col.operator("ezmod.transfer_from_mannequin", text="🧍 Перенести с манекена Arma 3", icon="OUTLINER_OB_ARMATURE")

        # --- Weight Paint Photoshop Suite (no tab switching!) ---
        if context.mode == "PAINT_WEIGHT":
            box_br = layout.box()
            box_br.label(text="🎨 Photoshop Weight Brush:", icon="BRUSH_DATA")

            row_tools = box_br.row(align=True)
            row_tools.scale_y = 1.3
            op = row_tools.operator("ezmod.ps_brush_action", text="🎨 Кисть (B)")
            op.action = "BRUSH"
            op = row_tools.operator("ezmod.ps_brush_action", text="🧹 Ластик (E)")
            op.action = "ERASER"
            op = row_tools.operator("ezmod.ps_brush_action", text="💧 Размыть (R)")
            op.action = "BLUR"
            op = row_tools.operator("ezmod.ps_brush_action", text="🪣 Залить (G)")
            op.action = "QUICK_FILL"

            wp_ts = getattr(context.tool_settings, "weight_paint", None)
            ups = getattr(wp_ts, "unified_paint_settings", None) or getattr(context.tool_settings, "unified_paint_settings", None)
            wp_brush = getattr(wp_ts, "brush", None)

            col_p = box_br.column(align=True)
            if ups and hasattr(ups, "size"):
                col_p.prop(ups, "size", text="Размер (Size)")
            elif wp_brush and hasattr(wp_brush, "size"):
                col_p.prop(wp_brush, "size", text="Размер (Size)")
            if wp_brush and hasattr(wp_brush, "strength"):
                col_p.prop(wp_brush, "strength", text="Сила (Opacity)", slider=True)

            row_h = box_br.row(align=True)
            row_h.label(text="Жёсткость:")
            cur_falloff = getattr(wp_brush, "curve_distance_falloff_preset", "SMOOTH") if wp_brush else "SMOOTH"
            op1 = row_h.operator("ezmod.ps_brush_action", text="Мягкая", depress=(cur_falloff == "SMOOTH"))
            op1.action = "SET_HARDNESS"
            op1.str_val = "SMOOTH"
            op2 = row_h.operator("ezmod.ps_brush_action", text="Средняя", depress=(cur_falloff in ("SHARP", "SPHERE")))
            op2.action = "SET_HARDNESS"
            op2.str_val = "SHARP"
            op3 = row_h.operator("ezmod.ps_brush_action", text="Жёсткая", depress=(cur_falloff == "CONSTANT"))
            op3.action = "SET_HARDNESS"
            op3.str_val = "CONSTANT"

            row_w = box_br.row(align=True)
            cur_w = ups.weight if (ups and hasattr(ups, "weight")) else (wp_brush.weight if wp_brush else 1.0)
            for w_val, w_txt in [(1.0, "100%"), (0.75, "75%"), (0.5, "50%"), (0.25, "25%"), (0.0, "0% Стереть")]:
                op_w = row_w.operator("ezmod.ps_brush_action", text=w_txt, depress=(abs(cur_w - w_val) < 0.05))
                op_w.action = "SET_WEIGHT"
                op_w.float_val = w_val

            row_safe = box_br.row(align=True)
            if wp_brush and hasattr(wp_brush, "use_frontface"):
                row_safe.prop(wp_brush, "use_frontface", text="Не насквозь", toggle=True, icon="HIDE_OFF")
            if context.tool_settings:
                row_safe.prop(context.tool_settings, "use_auto_normalize", text="Авто-норм.", toggle=True)
            if context.object and hasattr(context.object.data, "use_mirror_x"):
                row_safe.prop(context.object.data, "use_mirror_x", text="X", toggle=True)

            if context.object and context.object.type == "MESH":
                vg = context.object.vertex_groups.active
                row_b = box_br.row()
                row_b.label(text=f"Текущая кость: {vg.name if vg else 'Не выбрана'}", icon="BONE_DATA")
                row_b.label(text="(Ctrl+ЛКМ = клик по кости)", icon="INFO")

        col_s = layout.column(align=True)
        col_s.operator("ezmod.smooth_weights", text="✨ Сгладить + Лимит 4 кости", icon="SMOOTHCURVE")
        row_arma_tools = col_s.row(align=True)
        row_arma_tools.operator("ezmod.auto_generate_lods", text="⚡ Авто-Лодинг", icon="MOD_DECIM")
        has_empty_vp = bool(bpy.data.objects.get("LOD_View_Pilot_Empty"))
        row_arma_tools.operator(
            "ezmod.toggle_first_person_hide",
            text="👁 1-е лицо: Скрыто" if has_empty_vp else "👁 1-е лицо: Видно",
            icon="HIDE_ON" if has_empty_vp else "HIDE_OFF",
            depress=has_empty_vp,
        )
        row_tools = col_s.row(align=True)
        row_tools.operator("ezmod.sent_mirror_weights", text="🪞 Зеркало L↔R")
        row_tools.operator("ezmod.sent_clean_empty_vgroups", text="🧹 Пустые")

        box_rig = layout.box()
        box_rig.label(text="Жёстко к 1 кости (объект / выделение):", icon="BONE_DATA")
        row_b = box_rig.row(align=True)
        row_b.prop(props, "sent_target_bone", text="")
        row_b.operator("ezmod.sent_rigid_to_bone", text="Привязать", icon="PINNED")

        row_pose = layout.row(align=True)
        row_pose.operator("ezmod.test_pose", text="🤸 Тест позы", icon="POSE_HLT")
        row_pose.operator("ezmod.reset_pose", text="🔄 Сброс", icon="LOOP_BACK")


class EZMOD_PT_dedicated_tab_panel(bpy.types.Panel):
    bl_idname = "EZMOD_PT_dedicated_tab_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EZ_MOD"
    bl_label = "⚡ EZ_MOD: Своя снаряга в 1 клик"

    def draw(self, context):
        draw_ez_mod_ui(self.layout, context)


classes = (
    EZModAceaxValueItem,
    EZModAceaxOptionItem,
    EZModProperties,
    EZMOD_OT_spawn_mannequin,
    EZMOD_OT_toggle_mannequin,
    EZMOD_OT_test_pose,
    EZMOD_OT_reset_pose,
    EZMOD_OT_weight_paint_mode,
    EZMOD_OT_spawn_ref_gear,
    EZMOD_OT_import_selected_model,
    EZMOD_OT_import_p3d_file,
    EZMOD_OT_set_gear_type,
    EZMOD_OT_sent_edit_to_weight_paint,
    EZMOD_OT_sent_transfer_selected,
    EZMOD_OT_sent_set_weight,
    EZMOD_OT_sent_set_brush,
    EZMOD_OT_ps_brush_action,
    EZMOD_OT_auto_generate_lods,
    EZMOD_OT_toggle_first_person_hide,
    EZMOD_OT_arma_setup_first_person_hide,
    EZMOD_OT_sent_smart_transfer,
    EZMOD_OT_sent_rigid_to_bone,
    EZMOD_OT_sent_mirror_weights,
    EZMOD_OT_sent_clean_empty_vgroups,
    EZMOD_OT_transfer_from_mannequin,
    EZMOD_OT_auto_fit_gloves,
    EZMOD_OT_auto_rig,
    EZMOD_OT_auto_rig_vest_rigid,
    EZMOD_OT_smooth_weights,
    EZMOD_OT_fix_multi_uv,
    EZMOD_OT_adapt_dxt5nm_normals,
    EZMOD_OT_make_glass_node,
    EZMOD_PT_shader_glass_panel,
    EZMOD_OT_aceax_add_option,
    EZMOD_OT_aceax_remove_option,
    EZMOD_OT_aceax_add_value,
    EZMOD_OT_aceax_remove_value,
    EZMOD_OT_aceax_capture_view,
    EZMOD_OT_aceax_clear_view,
    EZMOD_OT_aceax_select_value,
    EZMOD_OT_build_mod,
    EZMOD_OT_create_mod_folder,
    EZMOD_OT_open_mod_folder,
    ATBX_PT_ez_sent_lazy_panel,
    ATBX_PT_ez_mod_tools_panel,
    EZMOD_PT_dedicated_tab_panel,
)


addon_keymaps = []


def register_ps_keymaps():
    wm = bpy.context.window_manager
    kc = getattr(wm, "keyconfigs", None)
    if not kc or not getattr(kc, "addon", None):
        return
    km = kc.addon.keymaps.new(name="Weight Paint", space_type="EMPTY")
    kmi1 = km.keymap_items.new("ezmod.ps_brush_action", "B", "PRESS")
    kmi1.properties.action = "BRUSH"
    kmi2 = km.keymap_items.new("ezmod.ps_brush_action", "E", "PRESS")
    kmi2.properties.action = "ERASER"
    kmi3 = km.keymap_items.new("ezmod.ps_brush_action", "R", "PRESS")
    kmi3.properties.action = "BLUR"
    kmi4 = km.keymap_items.new("ezmod.ps_brush_action", "X", "PRESS")
    kmi4.properties.action = "TOGGLE_X"
    kmi5 = km.keymap_items.new("ezmod.ps_brush_action", "LEFT_BRACKET", "PRESS")
    kmi5.properties.action = "SIZE_STEP"
    kmi5.properties.float_val = -20.0
    kmi6 = km.keymap_items.new("ezmod.ps_brush_action", "RIGHT_BRACKET", "PRESS")
    kmi6.properties.action = "SIZE_STEP"
    kmi6.properties.float_val = 20.0
    addon_keymaps.append((km, [kmi1, kmi2, kmi3, kmi4, kmi5, kmi6]))


def unregister_ps_keymaps():
    for km, items in addon_keymaps:
        for it in items:
            try:
                km.keymap_items.remove(it)
            except Exception:
                pass
    addon_keymaps.clear()


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.ez_mod_props = bpy.props.PointerProperty(type=EZModProperties)
    if hasattr(bpy.types, "VIEW3D_PT_paint_weight_context_menu"):
        try:
            bpy.types.VIEW3D_PT_paint_weight_context_menu.prepend(draw_ps_brush_context_menu)
        except Exception:
            pass
    try:
        register_ps_keymaps()
    except Exception:
        pass


def unregister():
    try:
        unregister_ps_keymaps()
    except Exception:
        pass
    if hasattr(bpy.types, "VIEW3D_PT_paint_weight_context_menu"):
        try:
            bpy.types.VIEW3D_PT_paint_weight_context_menu.remove(draw_ps_brush_context_menu)
        except Exception:
            pass
    if hasattr(bpy.types.Scene, "ez_mod_props"):
        del bpy.types.Scene.ez_mod_props
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
