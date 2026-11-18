# -*- coding: utf-8 -*-
"""
EZ_MOD Core Builder for Arma 3
Handles:
1. Direct MLOD (.p3d) binary generation with LOD 1.0 + Geometry LOD (1e13, autocenter=0) + #UVSet# + Bone Selections
2. Automatic PNG/JPG/TGA -> PAA conversion via Bohemia Pal2PacE.exe
3. Automatic RVMAT generation
4. Automatic model.cfg (OFP2_ManSkeleton) + Binarization via binarize_x64.exe -> ODOL .p3d
5. Automatic config.cpp + CfgConvert.exe -> config.bin
6. Automatic PBO packing into !Workshop/@EZ_Custom_Gear/addons/<addon_name>.pbo
"""

import os
import sys
import re
import json
import struct
import shutil
import hashlib
import tempfile
import subprocess

WORKSHOP_DIR = r"Z:\SteamLibrary\steamapps\common\Arma 3\!Workshop"
EZ_MOD_DIR = os.path.join(WORKSHOP_DIR, "@EZ_Custom_Gear")
EZ_ADDONS_DIR = os.path.join(EZ_MOD_DIR, "addons")


def resolve_mod_dirs(mod_folder_name=None):
    """Resolves (mod_dir, addons_dir, clean_folder_name) inside Arma 3 !Workshop."""
    raw = (mod_folder_name or "").strip()
    raw = re.sub(r'[<>:"/\\|?*]', "_", raw).strip(" .")
    if not raw:
        raw = "@EZ_Custom_Gear"
    if not raw.startswith("@"):
        raw = "@" + raw
    mod_dir = os.path.join(WORKSHOP_DIR, raw)
    addons_dir = os.path.join(mod_dir, "addons")
    return mod_dir, addons_dir, raw


def ensure_mod_metadata(mod_dir, addons_dir, folder_name, author="EZ_MOD Builder"):
    """Creates @Mod/addons, mod.cpp, and meta.cpp if not already present."""
    os.makedirs(addons_dir, exist_ok=True)
    clean_title = folder_name.lstrip("@").replace("_", " ").strip() or "EZ_MOD Custom Gear"
    safe_author = str(author or "EZ_MOD Builder").replace('"', "'")
    mod_cpp_path = os.path.join(mod_dir, "mod.cpp")
    if not os.path.exists(mod_cpp_path):
        with open(mod_cpp_path, "w", encoding="utf-8") as f:
            f.write(
                f'name = "{clean_title}";\n'
                f'author = "{safe_author}";\n'
                f'description = "Custom equipment built directly from Blender via EZ_MOD";\n'
                f'actionName = "Blender EZ_MOD";\n'
            )
    meta_cpp_path = os.path.join(mod_dir, "meta.cpp")
    if not os.path.exists(meta_cpp_path):
        with open(meta_cpp_path, "w", encoding="utf-8") as f:
            f.write(f'protocol = 1;\npublishedid = 0;\nname = "{clean_title}";\n')

APP_DIR = r"Z:\aarma"
THUMBS_DIR = os.path.join(APP_DIR, ".cache_thumbs")
TOOLS_DIR = os.path.join(APP_DIR, "tools")
MANNEQUIN_P3D = os.path.join(TOOLS_DIR, "arma3_mannequin.p3d")

A3_TOOLS_DIR = r"Z:\SteamLibrary\steamapps\common\Arma 3 Tools"
PAL2PAC_DIR = os.path.join(A3_TOOLS_DIR, "TexView2")
PAL2PAC_EXE = os.path.join(PAL2PAC_DIR, "Pal2PacE.exe")
BINARIZE_DIR = os.path.join(A3_TOOLS_DIR, "Binarize")
BINARIZE_EXE = os.path.join(BINARIZE_DIR, "binarize_x64.exe")
CFGCONVERT_DIR = os.path.join(A3_TOOLS_DIR, "CfgConvert")
CFGCONVERT_EXE = os.path.join(CFGCONVERT_DIR, "CfgConvert.exe")


OFP2_SKELETON_BONES = [
    ("pelvis", ""),
    ("spine", "pelvis"),
    ("spine1", "spine"),
    ("spine2", "spine1"),
    ("spine3", "spine2"),
    ("neck", "spine3"),
    ("neck1", "neck"),
    ("head", "neck1"),
    ("face_hub", "head"),
    ("face_jawbone", "face_hub"),
    ("face_jowl", "face_jawbone"),
    ("face_chopright", "face_jawbone"),
    ("face_chopleft", "face_jawbone"),
    ("face_liplowermiddle", "face_jawbone"),
    ("face_liplowerleft", "face_jawbone"),
    ("face_liplowerright", "face_jawbone"),
    ("face_chin", "face_jawbone"),
    ("face_tongue", "face_jawbone"),
    ("face_cornerright", "face_hub"),
    ("face_cheeksideright", "face_cornerright"),
    ("face_cornerleft", "face_hub"),
    ("face_cheeksideleft", "face_cornerleft"),
    ("face_cheekfrontright", "face_hub"),
    ("face_cheekfrontleft", "face_hub"),
    ("face_cheekupperright", "face_hub"),
    ("face_cheekupperleft", "face_hub"),
    ("face_lipuppermiddle", "face_hub"),
    ("face_lipupperright", "face_hub"),
    ("face_lipupperleft", "face_hub"),
    ("face_nostrilright", "face_hub"),
    ("face_nostrilleft", "face_hub"),
    ("face_forehead", "face_hub"),
    ("face_browfrontright", "face_forehead"),
    ("face_browfrontleft", "face_forehead"),
    ("face_browmiddle", "face_forehead"),
    ("face_browsideright", "face_forehead"),
    ("face_browsideleft", "face_forehead"),
    ("face_eyelids", "face_hub"),
    ("face_eyelidupperright", "face_hub"),
    ("face_eyelidupperleft", "face_hub"),
    ("face_eyelidlowerright", "face_hub"),
    ("face_eyelidlowerleft", "face_hub"),
    ("eyeleft", "face_hub"),
    ("eyeright", "face_hub"),
    ("leftshoulder", "spine3"),
    ("leftarm", "leftshoulder"),
    ("leftarmroll", "leftarm"),
    ("leftforearm", "leftarmroll"),
    ("leftforearmroll", "leftforearm"),
    ("lefthand", "leftforearmroll"),
    ("lefthandring", "lefthand"),
    ("lefthandring1", "lefthandring"),
    ("lefthandring2", "lefthandring1"),
    ("lefthandring3", "lefthandring2"),
    ("lefthandpinky1", "lefthandring"),
    ("lefthandpinky2", "lefthandpinky1"),
    ("lefthandpinky3", "lefthandpinky2"),
    ("lefthandmiddle1", "lefthand"),
    ("lefthandmiddle2", "lefthandmiddle1"),
    ("lefthandmiddle3", "lefthandmiddle2"),
    ("lefthandindex1", "lefthand"),
    ("lefthandindex2", "lefthandindex1"),
    ("lefthandindex3", "lefthandindex2"),
    ("lefthandthumb1", "lefthand"),
    ("lefthandthumb2", "lefthandthumb1"),
    ("lefthandthumb3", "lefthandthumb2"),
    ("rightshoulder", "spine3"),
    ("rightarm", "rightshoulder"),
    ("rightarmroll", "rightarm"),
    ("rightforearm", "rightarmroll"),
    ("rightforearmroll", "rightforearm"),
    ("righthand", "rightforearmroll"),
    ("righthandring", "righthand"),
    ("righthandring1", "righthandring"),
    ("righthandring2", "righthandring1"),
    ("righthandring3", "righthandring2"),
    ("righthandpinky1", "righthandring"),
    ("righthandpinky2", "righthandpinky1"),
    ("righthandpinky3", "righthandpinky2"),
    ("righthandmiddle1", "righthand"),
    ("righthandmiddle2", "righthandmiddle1"),
    ("righthandmiddle3", "righthandmiddle2"),
    ("righthandindex1", "righthand"),
    ("righthandindex2", "righthandindex1"),
    ("righthandindex3", "righthandindex2"),
    ("righthandthumb1", "righthand"),
    ("righthandthumb2", "righthandthumb1"),
    ("righthandthumb3", "righthandthumb2"),
    ("weapon", "spine1"),
    ("launcher", "spine1"),
    ("camera", "pelvis"),
    ("leftupleg", "pelvis"),
    ("leftuplegroll", "leftupleg"),
    ("leftleg", "leftuplegroll"),
    ("leftlegroll", "leftleg"),
    ("leftfoot", "leftlegroll"),
    ("lefttoebase", "leftfoot"),
    ("rightupleg", "pelvis"),
    ("rightuplegroll", "rightupleg"),
    ("rightleg", "rightuplegroll"),
    ("rightlegroll", "rightleg"),
    ("rightfoot", "rightlegroll"),
    ("righttoebase", "rightfoot"),
]


def convert_weight_byte(w):
    """Converts 0.0..1.0 float weight to MLOD selection byte."""
    if w <= 0.001:
        return 0
    if w >= 0.999:
        return 1
    val = int(round(255 - 254 * w))
    if val >= 255:
        val = 254
    if val < 1:
        val = 1
    return val


SOLDIER_TECH_LODS_PATH = os.path.join(TOOLS_DIR, "soldier_tech_lods.bin")

# Exact proxy definitions from b_soldier_01_mlod.p3d:
# (proxy_name, [pt0_xyz, pt1_xyz, pt2_xyz], (face_v0_rel, face_v1_rel, face_v2_rel), bone_name)
SOLDIER_PROXY_TRIANGLES = [
    ("proxy:\\a3\\characters_f\\proxies\\weapon.001", [(-0.9999998807907104, 0.0, 0.0), (-0.9999998807907104, 2.0, 0.0), (-2.0, 0.0, -2.9802322387695312e-08)], (2, 0, 1), "weapon"),
    ("proxy:\\a3\\characters_f\\proxies\\pistol.001", [(-0.6942766904830933, -0.03899604082107544, 0.019161224365234375), (-0.795113205909729, -0.17091333866119385, -1.9849292039871216), (-1.4268553256988525, 0.6488316059112549, 0.011304259300231934)], (2, 0, 1), "righthand"),
    ("proxy:\\a3\\characters_f\\proxies\\binoculars.001", [(0.6358476877212524, -0.029462754726409912, 0.0776107907295227), (2.610171318054199, -0.3295292854309082, -0.031650394201278687), (0.6828182339668274, -0.08370912075042725, 1.0750350952148438)], (2, 0, 1), "lefthand"),
    ("proxy:\\a3\\characters_f\\proxies\\launcher.001", [(1.006129264831543, 0.002995789051055908, 0.0), (-0.9938708543777466, 0.0029959678649902344, 8.940696716308594e-08), (1.0061291456222534, -0.9970042705535889, 0.0)], (2, 0, 1), "launcher"),
    ("proxy:\\a3\\characters_f\\proxies\\nvg.001", [(-0.023606985807418823, 2.688358783721924, -0.07521933317184448), (-0.0376761257648468, 0.7288870811462402, -1.1523791551589966), (-0.03079092502593994, 0.6946232318878174, -0.1529979407787323)], (2, 0, 1), "head"),
    ("proxy:\\a3\\characters_f\\proxies\\flag.001", [(-0.4891083240509033, 2.4797403812408447, -0.0028963088989257812), (-0.17700159549713135, 0.5091757774353027, 0.13661664724349976), (-1.1644691228866577, 0.3552410900592804, 0.17143118381500244)], (1, 0, 2), "spine3"),
    ("proxy:\\a3\\characters_f\\proxies\\equipment.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\backpack.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\pistol_holstered.001", [(-0.25329530239105225, -0.1712261438369751, -0.05562639236450195), (-0.2977093458175659, -0.5558314323425293, -2.0177950859069824), (-1.2448196411132812, -0.04134011268615723, -0.058642029762268066)], (2, 0, 1), "rightupleg"),
    ("proxy:\\a3\\characters_f\\proxies\\head_male.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\glasses.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\headgear.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\hair.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\backpack2.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\radio.001", [(0.11616694927215576, 0.505519688129425, -0.08335158228874207), (0.4113193452358246, 1.8607286214828491, -1.5242866277694702), (0.27561116218566895, 1.2081362009048462, 0.6101233959197998)], (2, 0, 1), "spine3"),
    ("proxy:\\a3\\characters_f\\heads\\bysta.001", [(0.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)], (2, 0, 1), None),
    ("proxy:\\a3\\characters_f\\proxies\\hmd.001", [(-0.023606985807418823, 2.688358783721924, -0.07521933317184448), (-0.0376761257648468, 0.7288870811462402, -1.1523791551589966), (-0.03079092502593994, 0.6946232318878174, -0.1529979407787323)], (2, 0, 1), "head"),
]


def _serialize_visual_p3dm_lod(verts_all, nrms_all, faces_all, vgroups_all, proxy_selections, tex_path, rvmat_path, slot_paths, resolution):
    """Serializes a single visual P3DM LOD chunk (e.g. 1.0 or 1100.0 View Pilot)."""
    chunk = bytearray()
    num_pts = len(verts_all)
    num_nrms = len(nrms_all)
    num_faces = len(faces_all)

    chunk += b"P3DM" + struct.pack("<IIIIII", 0x1C, 0x100, num_pts, num_nrms, num_faces, 0)
    for x, y, z in verts_all:
        chunk += struct.pack("<fffi", float(x), float(y), float(z), 0)
    for nx, ny, nz in nrms_all:
        chunk += struct.pack("<fff", float(nx), float(ny), float(nz))

    default_tex_bytes = tex_path.encode("ascii", errors="ignore") + b"\x00"
    default_rvmat_bytes = rvmat_path.encode("ascii", errors="ignore") + b"\x00"

    slot_face_indices = {}
    slot_vert_indices = {}
    uv_stream = bytearray()

    for fi, face_tuple in enumerate(faces_all):
        if len(face_tuple) >= 4:
            v_idxs, n_idxs, uvs, mat_idx = face_tuple[0], face_tuple[1], face_tuple[2], int(face_tuple[3])
        else:
            v_idxs, n_idxs, uvs, mat_idx = face_tuple[0], face_tuple[1], face_tuple[2], 0

        if mat_idx == -1:
            s_name = None
            f_tex_b = b"\x00"
            f_rv_b = b"\x00"
            vset = None
        elif slot_paths and 0 <= mat_idx < len(slot_paths):
            s_name, s_tex, s_rv = slot_paths[mat_idx]
            f_tex_b = s_tex.encode("ascii", errors="ignore") + b"\x00"
            f_rv_b = s_rv.encode("ascii", errors="ignore") + b"\x00"
            slot_face_indices.setdefault(s_name, []).append(fi)
            vset = slot_vert_indices.setdefault(s_name, set())
        else:
            s_name = "camo"
            f_tex_b = default_tex_bytes
            f_rv_b = default_rvmat_bytes
            slot_face_indices.setdefault(s_name, []).append(fi)
            vset = slot_vert_indices.setdefault(s_name, set())

        sides = len(v_idxs)
        chunk += struct.pack("<I", sides)
        for i in range(4):
            if i < sides:
                vi = int(v_idxs[i])
                ni = int(n_idxs[i])
                if vset is not None:
                    vset.add(vi)
                u, v = float(uvs[i][0]), 1.0 - float(uvs[i][1])
                chunk += struct.pack("<iiff", vi, ni, u, v)
                uv_stream += struct.pack("<ff", u, v)
            else:
                chunk += struct.pack("<iiff", 0, 0, 0.0, 0.0)
        chunk += struct.pack("<I", 0)
        chunk += f_tex_b
        chunk += f_rv_b

    chunk += b"TAGG"
    uv_payload = struct.pack("<I", 0) + uv_stream
    chunk += b"\x01#UVSet#\x00" + struct.pack("<I", len(uv_payload)) + uv_payload

    for pk, pv in [("autocenter", "0"), ("lodnoshadow", "1")]:
        prop_payload = pk.encode("ascii").ljust(64, b"\x00") + pv.encode("ascii").ljust(64, b"\x00")
        chunk += b"\x01#Property#\x00" + struct.pack("<I", 128) + prop_payload

    for s_name, f_list in slot_face_indices.items():
        pt_bytes = bytearray(num_pts)
        face_bytes = bytearray(num_faces)
        for vi in slot_vert_indices.get(s_name, ()):
            if 0 <= vi < num_pts:
                pt_bytes[vi] = 1
        for fi in f_list:
            if 0 <= fi < num_faces:
                face_bytes[fi] = 1
        sel_payload = pt_bytes + face_bytes
        chunk += b"\x01" + s_name.encode("ascii", errors="ignore") + b"\x00" + struct.pack("<I", len(sel_payload)) + sel_payload

    for pname, (p_vlist, p_flist) in proxy_selections.items():
        pt_bytes = bytearray(num_pts)
        face_bytes = bytearray(num_faces)
        for vi in p_vlist:
            if 0 <= vi < num_pts:
                pt_bytes[vi] = 1
        for fi in p_flist:
            if 0 <= fi < num_faces:
                face_bytes[fi] = 1
        sel_payload = pt_bytes + face_bytes
        chunk += b"\x01" + pname.encode("ascii", errors="ignore") + b"\x00" + struct.pack("<I", len(sel_payload)) + sel_payload

    for gname, gweights in vgroups_all.items():
        if not gweights or gname in slot_face_indices:
            continue
        pt_bytes = bytearray(num_pts)
        face_bytes = bytearray(num_faces)
        has_any = False
        for vi, w in gweights.items():
            if 0 <= vi < num_pts:
                wb = convert_weight_byte(w)
                if wb > 0:
                    pt_bytes[vi] = wb
                    has_any = True
        if not has_any:
            continue
        sel_payload = pt_bytes + face_bytes
        chunk += b"\x01" + gname.encode("ascii", errors="ignore") + b"\x00" + struct.pack("<I", len(sel_payload)) + sel_payload

    chunk += b"\x01#EndOfFile#\x00" + struct.pack("<I", 0)
    chunk += struct.pack("<f", float(resolution))
    return chunk


def write_mlod_p3d(filepath, vertices, normals, faces, vertex_groups, tex_path="", rvmat_path="", slot_paths=None, empty_view_pilot=False, include_soldier_proxies=False):
    """
    Writes a complete Bohemia MLOD .p3d with:
    - LOD 0 (Resolution 1.000): visual mesh + #UVSet# + #Property# (lodnoshadow=1, autocenter=0) + Named Selections
    - For UNIFORM (include_soldier_proxies=True):
        * Appends the 17 soldier proxies with exact Bohemia point-index order (Origin = min vertex index!)
        * Adds View Pilot (1100.0) LOD with head-less proxies (and omits unproxied face triangles so no white triangle ever appears in 1st person!)
        * Appends all 10 official Arma 3 soldier technical LODs from `soldier_tech_lods.bin`
    """
    def _build_lod_arrays(proxy_list):
        v_list = list(vertices)
        n_list = list(normals)
        f_list = list(faces)
        vg_dict = {k: dict(v) for k, v in vertex_groups.items()}
        p_sels = {}
        for pname, p_coords, f_rel, p_bone in proxy_list:
            base_v = len(v_list)
            base_n = len(n_list)
            f_idx = len(f_list)
            for cx, cy, cz in p_coords:
                v_list.append((cx, cy, cz))
                n_list.append((-0.0, -0.0, -0.0))
            r0, r1, r2 = f_rel
            f_list.append(
                (
                    [base_v + r0, base_v + r1, base_v + r2],
                    [base_n + r0, base_n + r1, base_n + r2],
                    [(0.0, 1.0), (0.0, 1.0), (0.0, 1.0)],
                    -1,
                )
            )
            p_sels[pname] = ([base_v, base_v + 1, base_v + 2], [f_idx])
            if p_bone:
                bdict = vg_dict.setdefault(p_bone, {})
                for vi in (base_v, base_v + 1, base_v + 2):
                    bdict[vi] = 1.0
        return v_list, n_list, f_list, vg_dict, p_sels

    verts_all, nrms_all, faces_all, vgroups_all, proxy_selections = _build_lod_arrays(
        SOLDIER_PROXY_TRIANGLES if include_soldier_proxies else []
    )

    tech_blob = b""
    if include_soldier_proxies and os.path.exists(SOLDIER_TECH_LODS_PATH):
        with open(SOLDIER_TECH_LODS_PATH, "rb") as tf:
            tech_blob = tf.read()

    out = bytearray()
    if include_soldier_proxies and tech_blob:
        # LOD 0 (1.0) + LOD 1 (View Pilot 1100.0) + 10 Technical Soldier LODs = 12 LODs
        num_lods = 12
        out += b"MLOD" + struct.pack("<II", 257, num_lods)
        out += _serialize_visual_p3dm_lod(
            verts_all, nrms_all, faces_all, vgroups_all, proxy_selections, tex_path, rvmat_path, slot_paths, 1.0
        )
        # View Pilot (1100.0): build ONLY the 1st-person proxy triangles (no orphan triangles without proxy:... tags!)
        vp_exclude = {"head_male.001", "bysta.001", "hair.001", "headgear.001", "glasses.001", "nvg.001", "hmd.001"}
        vp_proxy_defs = [
            p for p in SOLDIER_PROXY_TRIANGLES if not any(p[0].lower().endswith(ex) for ex in vp_exclude)
        ]
        vp_v, vp_n, vp_f, vp_vg, vp_psels = _build_lod_arrays(vp_proxy_defs)
        out += _serialize_visual_p3dm_lod(
            vp_v, vp_n, vp_f, vp_vg, vp_psels, tex_path, rvmat_path, slot_paths, 1100.0
        )
        out += tech_blob
    else:
        num_lods = 3 if empty_view_pilot else 2
        out += b"MLOD" + struct.pack("<II", 257, num_lods)
        out += _serialize_visual_p3dm_lod(
            verts_all, nrms_all, faces_all, vgroups_all, proxy_selections, tex_path, rvmat_path, slot_paths, 1.0
        )
        if empty_view_pilot:
            out += b"P3DM" + struct.pack("<IIIIII", 0x1C, 0x100, 0, 0, 0, 0)
            out += b"TAGG"
            out += b"\x01#EndOfFile#\x00" + struct.pack("<I", 0)
            out += struct.pack("<f", 1100.0)

        out += b"P3DM" + struct.pack("<IIIIII", 0x1C, 0x100, 0, 0, 0, 0)
        out += b"TAGG"
        prop_payload = b"autocenter".ljust(64, b"\x00") + b"0".ljust(64, b"\x00")
        out += b"\x01#Property#\x00" + struct.pack("<I", 128) + prop_payload
        out += b"\x01#EndOfFile#\x00" + struct.pack("<I", 0)
        out += struct.pack("<f", 1e13)

    with open(filepath, "wb") as f:
        f.write(out)
    return filepath


def create_solid_png(filepath, rgb=(85, 95, 75), alpha=255):
    """Creates a minimal valid 64x64 solid color PNG (with optional alpha) if no texture was provided."""
    import zlib
    w, h = 64, 64
    a_byte = max(0, min(255, int(alpha)))
    raw_data = bytearray()
    for _ in range(h):
        raw_data.append(0)  # filter type 0
        for _ in range(w):
            raw_data.extend([rgb[0], rgb[1], rgb[2], a_byte])

    def png_chunk(chunk_type, data):
        chunk = chunk_type + data
        return struct.pack('>I', len(data)) + chunk + struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw_data), 9)
    png_bytes = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', ihdr) + png_chunk(b'IDAT', idat) + png_chunk(b'IEND', b'')
    with open(filepath, 'wb') as f:
        f.write(png_bytes)
    return filepath


def apply_glass_tint_and_alpha_png(src_path, output_dir=None, alpha=0.42, tint_rgb=None):
    """
    Prepares a semi-transparent RGBA PNG for Arma 3 glass / lens / visor (*_ca.paa):
    - Multiplies RGB by tint_rgb (if provided, e.g. from Blender Multiply node or Base Color)
    - Sets Alpha channel to `alpha` (0.05..0.95) so Pal2PacE.exe creates a true DXT5 translucent *_ca.paa!
    """
    out_dir = output_dir or THUMBS_DIR
    os.makedirs(out_dir, exist_ok=True)
    a_val = max(0.05, min(0.95, float(alpha)))
    a_byte = int(round(a_val * 255.0))
    t_rgb = tuple(int(max(0, min(255, c))) for c in (tint_rgb or (255, 255, 255)))

    if not src_path or not os.path.exists(src_path):
        dst_solid = os.path.join(out_dir, f"glass_solid_{t_rgb[0]}_{t_rgb[1]}_{t_rgb[2]}_a{a_byte}.png")
        return create_solid_png(dst_solid, rgb=t_rgb, alpha=a_byte)

    st = os.stat(src_path)
    h_tag = hashlib.md5(
        f"{os.path.abspath(src_path)}:{st.st_size}:{int(st.st_mtime)}:{a_byte}:{t_rgb}".encode("utf-8")
    ).hexdigest()[:10]
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    dst_png = os.path.join(out_dir, f"{base_name}_glass_ca_{h_tag}.png")
    if os.path.exists(dst_png) and os.path.getsize(dst_png) > 0:
        return dst_png

    try:
        from PIL import Image
        import numpy as np
        img = Image.open(src_path).convert("RGBA")
        arr = np.array(img).astype(np.float32)
        if t_rgb != (255, 255, 255):
            arr[:, :, 0] = np.clip(arr[:, :, 0] * (t_rgb[0] / 255.0), 0, 255)
            arr[:, :, 1] = np.clip(arr[:, :, 1] * (t_rgb[1] / 255.0), 0, 255)
            arr[:, :, 2] = np.clip(arr[:, :, 2] * (t_rgb[2] / 255.0), 0, 255)
        arr[:, :, 3] = float(a_byte)
        Image.fromarray(arr.astype(np.uint8), mode="RGBA").save(dst_png)
        return dst_png
    except Exception:
        pass

    dst_solid = os.path.join(out_dir, f"glass_solid_{t_rgb[0]}_{t_rgb[1]}_{t_rgb[2]}_a{a_byte}.png")
    return create_solid_png(dst_solid, rgb=t_rgb, alpha=a_byte)


def generate_model_cfg(model_name, slot_names=None, extra_models=None):
    if not slot_names:
        slot_names = ["camo", "camo1", "camo2"]
    sec_list = list(dict.fromkeys(list(slot_names) + ["camo", "camo1", "camo2", "personality"]))
    sec_str = ", ".join(f'"{s}"' for s in sec_list)
    bones_str = ",\n            ".join(f'"{b}", "{p}"' for b, p in OFP2_SKELETON_BONES)
    model_names = [model_name]
    if extra_models:
        for m in extra_models:
            if m and m not in model_names:
                model_names.append(m)
    model_classes_str = "\n    ".join(f"class {m}: ArmaMan {{}};" for m in model_names)
    return f"""class CfgSkeletons {{
    class Default {{
        isDiscrete = 1;
        skeletonInherit = "";
        skeletonBones[] = {{}};
    }};
    class OFP2_ManSkeleton: Default {{
        isDiscrete = 0;
        skeletonInherit = "";
        skeletonBones[] = {{
            {bones_str}
        }};
    }};
}};

class CfgModels {{
    class Default {{
        sectionsInherit = "";
        sections[] = {{}};
        skeletonName = "";
    }};
    class ArmaMan: Default {{
        htMin = 60;
        htMax = 1800;
        afMax = 30;
        mfMax = 0;
        mFact = 1;
        tBody = 37;
        sections[] = {{{sec_str}}};
        skeletonName = "OFP2_ManSkeleton";
        class Animations {{}};
    }};
    {model_classes_str}
}};
"""


def generate_rvmat(addon_prefix, has_normal=False, slot_name="camo", is_glass=False):
    nohq_tex = f"{addon_prefix}\\data\\{slot_name}_nohq.paa" if has_normal else "#(argb,8,8,3)color(0.5,0.5,1,1,NOHQ)"
    if is_glass:
        return f"""ambient[] = {{1, 1, 1, 1}};
diffuse[] = {{1, 1, 1, 1}};
forcedDiffuse[] = {{0, 0, 0, 0}};
emmisive[] = {{0, 0, 0, 1}};
specular[] = {{1, 1, 1, 1}};
specularPower = 1000;
PixelShaderID = "Super";
VertexShaderID = "Super";
class StageTI {{
    texture = "#(argb,8,8,3)color(0,0,0,0)";
}};
class Stage1 {{
    texture = "{nohq_tex}";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage2 {{
    texture = "#(argb,8,8,3)color(0.5,0.5,0.5,1,DT)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage3 {{
    texture = "#(argb,8,8,3)color(0,0,0,0,MC)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage4 {{
    texture = "#(argb,8,8,3)color(1,1,1,1,AS)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage5 {{
    texture = "#(argb,8,8,3)color(1,1,1,1,SMDI)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage6 {{
    texture = "#(ai,64,64,1)fresnel(0.5,0.2)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage7 {{
    texture = "a3\\data_f\\env_co.paa";
    useWorldEnvMap = "true";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
"""
    return f"""ambient[] = {{1, 1, 1, 1}};
diffuse[] = {{1, 1, 1, 1}};
forcedDiffuse[] = {{0, 0, 0, 0}};
emmisive[] = {{0, 0, 0, 1}};
specular[] = {{0.12, 0.12, 0.12, 1}};
specularPower = 35;
PixelShaderID = "Super";
VertexShaderID = "Super";
class StageTI {{
    texture = "#(argb,8,8,3)color(0.5,0,0,0,TI)";
}};
class Stage1 {{
    texture = "{nohq_tex}";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage2 {{
    texture = "#(argb,8,8,3)color(0.5,0.5,0.5,1,DT)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage3 {{
    texture = "#(argb,8,8,3)color(0,0,0,0,MC)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage4 {{
    texture = "#(argb,8,8,3)color(1,1,1,1,AS)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage5 {{
    texture = "#(argb,8,8,3)color(1,0.25,1,1,SMDI)";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
class Stage6 {{
    texture = "#(ai,64,64,1)fresnel(1,0.7)";
    uvSource = "none";
}};
class Stage7 {{
    texture = "a3\\data_f\\env_co.paa";
    useWorldEnvMap = "true";
    uvSource = "tex";
    class uvTransform {{
        aside[] = {{1, 0, 0}};
        up[] = {{0, 1, 0}};
        dir[] = {{0, 0, 0}};
        pos[] = {{0, 0, 0}};
    }};
}};
"""


def ensure_opaque_rgb_png(src_path, output_dir=None):
    """
    In Tarkov (Unity), Source, and Call of Duty assets, Diffuse (_D / _diff) and standard Normal (_n / _nml)
    textures often pack Glossiness / Smoothness into the Alpha channel (where matte surfaces have Alpha < 128).
    When Bohemia Pal2PacE.exe converts an RGBA PNG to *_co.paa (DXT1 1-bit alpha), it zeros out RGB to pure
    BLACK (0,0,0) on every pixel with Alpha < 128, creating black speckled 'crust' across the model!
    This function strips the Alpha channel (converts RGBA -> opaque RGB) before Pal2PacE.exe runs.
    """
    if not src_path or not os.path.exists(src_path):
        return src_path
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in (".png", ".tga", ".jpg", ".jpeg"):
        return src_path

    out_dir = output_dir or THUMBS_DIR
    os.makedirs(out_dir, exist_ok=True)
    st = os.stat(src_path)
    h_tag = hashlib.md5(f"{os.path.abspath(src_path)}:{st.st_size}:{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:10]
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    dst_png = os.path.join(out_dir, f"{base_name}_opaque_{h_tag}.png")

    if os.path.exists(dst_png) and os.path.getsize(dst_png) > 0:
        return dst_png

    try:
        from PIL import Image
        img = Image.open(src_path)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.convert("RGB").save(dst_png)
            return dst_png
        return src_path
    except ImportError:
        pass
    except Exception:
        return src_path

    py_cmd = (
        "import sys; from PIL import Image; img = Image.open(sys.argv[1]); "
        "img.convert('RGB').save(sys.argv[2]) if img.mode in ('RGBA','LA','P') else sys.exit(2)"
    )
    try:
        res = subprocess.run(["python", "-c", py_cmd, src_path, dst_png], capture_output=True, timeout=25)
        if res.returncode == 0 and os.path.exists(dst_png):
            return dst_png
    except Exception:
        pass
    return src_path


def unpack_dxt5nm_if_needed(src_path, output_dir=None, invert_green=False, strength=1.0):
    """
    Detects if src_path is a DXT5nm normal map (Tarkov/Unity format: Red=255/gloss, Green=Y, Alpha=X).
    If detected, reconstructs Z = sqrt(1 - X^2 - Y^2) with optional strength factor and exports a clean
    standard OpenGL Tangent-space Normal Map (RGB: Red=X, Green=Y, Blue=Z).
    If already a standard normal map with RGBA alpha glossiness, strips the alpha channel to opaque RGB.
    """
    if not src_path or not os.path.exists(src_path):
        return src_path
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in ('.png', '.tga', '.jpg', '.jpeg'):
        return src_path

    out_dir = output_dir or os.path.dirname(src_path)
    os.makedirs(out_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    if base_name.endswith("_std_norm"):
        return src_path
    str_tag = f"_s{int(round(strength * 100))}" if abs(strength - 1.0) > 0.01 else ""
    dst_png = os.path.join(out_dir, f"{base_name}{str_tag}_std_norm.png")

    if os.path.exists(dst_png) and os.path.getmtime(dst_png) >= os.path.getmtime(src_path):
        return dst_png

    py_script = """
import sys, os
from PIL import Image
import numpy as np

src_fp = sys.argv[1]
dst_fp = sys.argv[2]
inv_g = sys.argv[3].lower() == 'true'
strength = float(sys.argv[4])

try:
    img = Image.open(src_fp)
    arr = np.array(img).astype(np.float32)
    if arr.ndim != 3 or arr.shape[2] != 4:
        sys.exit(2)
    r, g, b, a = arr[:,:,0], arr[:,:,1], arr[:,:,2], arr[:,:,3]
    is_dxt5 = (r.mean() > 220 and r.std() < 15.0 and a.std() > 3.0 and g.std() > 3.0)
    if not is_dxt5:
        if not (a.std() > 8.0 and g.std() > 8.0 and b.std() < 15.0):
            sys.exit(2)
    x_n = (a / 255.0 - 0.5) * 2.0 * strength
    y_raw = (g / 255.0 - 0.5) * 2.0 * strength
    y_n = -y_raw if inv_g else y_raw
    z_sq = np.clip(1.0 - (x_n**2 + y_n**2), 0.0, 1.0)
    z_n = np.sqrt(z_sq)

    r_out = np.clip((x_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
    g_out = np.clip((y_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
    b_out = np.clip((z_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
    out_img = Image.fromarray(np.stack([r_out, g_out, b_out], axis=2), mode='RGB')
    out_img.save(dst_fp)
    sys.exit(0)
except Exception:
    sys.exit(1)
"""
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(src_path)
        arr = np.array(img).astype(np.float32)
        if arr.ndim == 3 and arr.shape[2] == 4:
            r, g, b, a = arr[:,:,0], arr[:,:,1], arr[:,:,2], arr[:,:,3]
            is_dxt5 = (r.mean() > 220 and r.std() < 15.0 and a.std() > 3.0 and g.std() > 3.0)
            if not is_dxt5:
                if (a.std() > 8.0 and g.std() > 8.0 and b.std() < 15.0):
                    is_dxt5 = True
            if is_dxt5:
                x_n = (a / 255.0 - 0.5) * 2.0 * float(strength)
                y_raw = (g / 255.0 - 0.5) * 2.0 * float(strength)
                y_n = -y_raw if invert_green else y_raw
                z_sq = np.clip(1.0 - (x_n**2 + y_n**2), 0.0, 1.0)
                z_n = np.sqrt(z_sq)
                r_out = np.clip((x_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
                g_out = np.clip((y_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
                b_out = np.clip((z_n * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
                out_img = Image.fromarray(np.stack([r_out, g_out, b_out], axis=2), mode='RGB')
                out_img.save(dst_png)
                return dst_png
            else:
                return ensure_opaque_rgb_png(src_path, output_dir=out_dir)
    except ImportError:
        pass

    try:
        res = subprocess.run(
            ["python", "-c", py_script, src_path, dst_png, str(invert_green), str(strength)],
            capture_output=True,
            timeout=30
        )
        if res.returncode == 0 and os.path.exists(dst_png):
            return dst_png
    except Exception:
        pass

    return ensure_opaque_rgb_png(src_path, output_dir=out_dir)


def generate_config_cpp(
    addon_name,
    class_name,
    display_name,
    author,
    gear_type,
    armor=10,
    mass=20,
    max_load=160,
    slot_names=None,
    aceax_cfg=None,
    slot_tex_files=None,
):
    if not slot_names:
        slot_names = ["camo"]
    model_p3d = f"\\{addon_name}\\{addon_name}.p3d"
    hs_str = ", ".join(f'"{s}"' for s in slot_names)
    if slot_tex_files and len(slot_tex_files) == len(slot_names):
        hst_str = ", ".join(f'"\\{addon_name}\\data\\{tf}"' for tf in slot_tex_files)
    else:
        hst_str = ", ".join(f'"\\{addon_name}\\data\\{s}_co.paa"' for s in slot_names)
    desc = f"EZ_MOD Custom {gear_type} ({display_name})"

    xtd_root_block = ""
    xtd_item_block = ""
    req_aceax = ""
    if aceax_cfg and aceax_cfg.get("enabled"):
        m_id = re.sub(r"[^a-zA-Z0-9_]", "_", aceax_cfg.get("model", "EZ_Custom_Pack")).strip("_") or "EZ_Custom_Pack"
        m_label = aceax_cfg.get("label", display_name).replace('"', "'")
        req_aceax = ', "aceax_gearinfo"'
        root_cat = "CfgVehicles" if gear_type == "BACKPACK" else ("CfgGlasses" if gear_type == "GOGGLES" else "CfgWeapons")

        opts = aceax_cfg.get("options") or []
        if not opts:
            camo_val = (aceax_cfg.get("camo", "Default") or "Default").replace('"', "'")
            var_val = (aceax_cfg.get("variant", "Base") or "Base").replace('"', "'")
            opts = [
                {"id": "Camo", "label": "Camo", "values": [camo_val], "selected": camo_val},
                {"id": "Variant", "label": "Variant", "values": [var_val], "selected": var_val},
            ]

        opt_ids = []
        opt_classes = []
        info_lines = [f'            model = "{m_id}";']
        for opt in opts:
            oid = re.sub(r"[^a-zA-Z0-9_]", "_", str(opt.get("id", "Option"))).strip("_") or "Option"
            olabel = str(opt.get("label") or oid).replace('"', "'")
            vals = [str(v).replace('"', "'").strip() for v in (opt.get("values") or []) if str(v).strip()]
            sel = str(opt.get("selected") or (vals[0] if vals else "Base")).replace('"', "'").strip()
            if sel and sel not in vals:
                vals.append(sel)
            if not vals:
                vals = ["Base"]
            opt_ids.append(f'"{oid}"')
            vals_str = ", ".join(f'"{v}"' for v in vals)
            opt_classes.append(
                f"""            class {oid} {{
                label = "{olabel}";
                alwaysSelectable = 1;
                values[] = {{{vals_str}}};
            }};"""
            )
            info_lines.append(f'            {oid} = "{sel}";')

        opts_arr_str = ", ".join(opt_ids)
        opt_classes_str = "\n".join(opt_classes)
        info_lines_str = "\n".join(info_lines)

        xtd_root_block = f"""
class XtdGearModels {{
    class {root_cat} {{
        class {m_id} {{
            label = "{m_label}";
            author = "{author}";
            options[] = {{{opts_arr_str}}};
{opt_classes_str}
        }};
    }};
}};
"""
        xtd_item_block = f"""
        class XtdGearInfo {{
{info_lines_str}
        }};"""

    if gear_type == "HEADGEAR":
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{}};
        weapons[] = {{"{class_name}"}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgWeapons {{
    class ItemCore;
    class HeadgearItem;
    class {class_name}: ItemCore {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: HeadgearItem {{
            mass = {int(mass)};
            uniformModel = "{model_p3d}";
            modelSides[] = {{3, 1, 2, 0}};
            hiddenSelections[] = {{{hs_str}}};
            class HitpointsProtectionInfo {{
                class Head {{
                    hitpointName = "HitHead";
                    armor = {int(armor)};
                    passThrough = 0.5;
                }};
            }};
        }};
    }};
}};
"""
    elif gear_type == "VEST":
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{}};
        weapons[] = {{"{class_name}"}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgWeapons {{
    class ItemCore;
    class Vest_Camo_Base: ItemCore {{
        class ItemInfo;
    }};
    class {class_name}: Vest_Camo_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: ItemInfo {{
            uniformModel = "{model_p3d}";
            containerClass = "Supply{min(200, max(20, int(max_load)))}";
            mass = {int(mass)};
            hiddenSelections[] = {{{hs_str}}};
            class HitpointsProtectionInfo {{
                class Chest {{
                    HitpointName = "HitChest";
                    armor = {int(armor)};
                    PassThrough = 0.2;
                }};
                class Diaphragm {{
                    HitpointName = "HitDiaphragm";
                    armor = {int(armor)};
                    PassThrough = 0.2;
                }};
                class Abdomen {{
                    hitpointName = "HitAbdomen";
                    armor = {int(armor)};
                    passThrough = 0.2;
                }};
                class Body {{
                    hitpointName = "HitBody";
                    passThrough = 0.2;
                }};
            }};
        }};
    }};
}};
"""
    elif gear_type == "UNIFORM":
        soldier_class = f"{class_name}_Soldier"
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{"{soldier_class}"}};
        weapons[] = {{"{class_name}"}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgVehicles {{
    class B_Soldier_base_F;
    class {soldier_class}: B_Soldier_base_F {{
        scope = 1;
        author = "{author}";
        displayName = "{display_name}";
        uniformClass = "{class_name}";
        model = "{model_p3d}";
        modelSides[] = {{3, 1, 2, 0}};
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
    }};
}};
class CfgWeapons {{
    class Uniform_Base;
    class UniformItem;
    class {class_name}: Uniform_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "\\A3\\Characters_F\\Common\\Suitpacks\\suitpack_universal_F.p3d";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: UniformItem {{
            uniformModel = "-";
            uniformClass = "{soldier_class}";
            containerClass = "Supply{min(200, max(20, int(max_load)))}";
            mass = {int(mass)};
        }};
    }};
}};
"""
    elif gear_type == "BACKPACK":
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{"{class_name}"}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Weapons_F", "A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgVehicles {{
    class Bag_Base;
    class {class_name}: Bag_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
        maximumLoad = {int(max_load)};
        mass = {int(mass)};{xtd_item_block}
    }};
}};
"""
    elif gear_type == "GOGGLES":
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgGlasses {{
    class None;
    class {class_name}: None {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
        mass = {int(mass)};{xtd_item_block}
    }};
}};
"""
    elif gear_type == "FACE":
        head_class = f"{class_name}_HEAD"
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"}};
    }};
}};
class CfgHeads {{
    class DefaultHead_A3;
    class {head_class}: DefaultHead_A3 {{
        model = "{model_p3d}";
        class Wounds {{
            tex[] = {{}};
            mat[] = {{
                "{addon_name}\\data\\camo.rvmat",
                "{addon_name}\\data\\camo.rvmat",
                "{addon_name}\\data\\camo.rvmat"
            }};
        }};
    }};
}};
class CfgFaces {{
    class Default;
    class Man_A3: Default {{
        class DefaultFace;
        class {class_name}: DefaultFace {{
            author = "{author}";
            displayname = "{display_name}";
            head = "{head_class}";
            texture = "\\{addon_name}\\data\\camo_co.paa";
            material = "\\{addon_name}\\data\\camo.rvmat";
            textureHL = "\\A3\\Characters_F\\Heads\\Data\\hl_White_hairy_1_co.paa";
            materialHL = "\\A3\\Characters_F\\Heads\\Data\\hl_White_hairy_muscular.rvmat";
            disabled = 0;
            identityTypes[] = {{"Head_NATO", "Head_Euro"}};
        }};
    }};
}};
"""
    else:  # NVG / ARMBAND
        return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{}};
        weapons[] = {{"{class_name}"}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
class CfgWeapons {{
    class NVGoggles;
    class {class_name}: NVGoggles {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        visionMode[] = {{"Normal"}};
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo {{
            type = 616;
            hmdType = 0;
            uniformModel = "{model_p3d}";
            modelOff = "{model_p3d}";
            mass = {int(mass)};
            hiddenSelections[] = {{{hs_str}}};
        }};
    }};
}};
"""


def pack_folder_to_pbo(source_dir, output_pbo, prefix_name):
    """Packs a folder into a Bohemia PBO with Vers header + SHA1 checksum."""
    files_to_pack = []
    for root, _, files in os.walk(source_dir):
        for fn in sorted(files):
            if fn.lower() == "model.cfg":
                continue
            full_p = os.path.join(root, fn)
            rel_p = os.path.relpath(full_p, source_dir).replace("/", "\\")
            files_to_pack.append((rel_p, full_p))

    os.makedirs(os.path.dirname(output_pbo), exist_ok=True)
    body = bytearray()

    # 1. Vers header entry
    body += b"\x00" + struct.pack("<IIIII", 0x56657273, 0, 0, 0, 0)
    body += b"prefix\x00" + prefix_name.encode("ascii") + b"\x00"
    body += b"\x00"  # end of properties

    # 2. File table
    file_blobs = []
    for rel_p, full_p in files_to_pack:
        with open(full_p, "rb") as f:
            b = f.read()
        file_blobs.append(b)
        body += rel_p.encode("ascii") + b"\x00"
        body += struct.pack("<IIIII", 0, len(b), 0, int(os.path.getmtime(full_p)), len(b))

    # 3. End of file table
    body += b"\x00" + struct.pack("<IIIII", 0, 0, 0, 0, 0)

    # 4. File contents
    for b in file_blobs:
        body += b

    # 5. SHA1 footer
    sha1 = hashlib.sha1(body).digest()
    with open(output_pbo, "wb") as f:
        f.write(body)
        f.write(b"\x00")
        f.write(sha1)

    return output_pbo


def build_ez_mod(
    slug,
    display_name,
    gear_type,
    vertices,
    normals,
    faces,
    vertex_groups,
    diffuse_img_path=None,
    normal_img_path=None,
    author="EZ_MOD Creator",
    armor=15,
    mass=15,
    max_load=160,
    material_slots=None,
    aceax_cfg=None,
    mod_folder_name=None,
    empty_view_pilot=None,
):
    """
    Complete end-to-end 1-click build pipeline:
    Mesh -> MLOD -> PAA + RVMAT -> Binarize ODOL -> CfgConvert config.bin -> Pack PBO -> Register in @EZ_Custom_Gear
    material_slots: optional list of (diff_img_path, norm_img_path) per Blender material slot (camo, camo1, camo2...)
    """
    clean_slug = re.sub(r"[^a-zA-Z0-9_]", "_", slug).lower().strip("_")
    if not clean_slug:
        clean_slug = "custom_item"
    addon_name = f"ez_{clean_slug}"
    class_name = f"EZ_{clean_slug.upper()}"

    work_dir = tempfile.mkdtemp(prefix=f"ezmod_{addon_name}_")
    src_dir = os.path.join(work_dir, "src", addon_name)
    dst_dir = os.path.join(work_dir, "dst", addon_name)
    os.makedirs(os.path.join(src_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "data"), exist_ok=True)

    try:
        if not material_slots:
            material_slots = [(diffuse_img_path, normal_img_path)]

        slot_paths = []
        slot_names = []
        slot_tex_files = []

        def convert_or_copy_paa(src_img_path, dst_paa_path, fallback_png_path=None):
            """
            Fast-path PAA resolver:
            1. If src_img_path is already .paa -> direct copy (0.001s)
            2. If sibling .paa exists next to src_img_path (e.g. in .cache_thumbs) -> direct copy (0.001s)
            3. If cached .paa by file hash exists in THUMBS_DIR -> direct copy (0.001s)
            4. Otherwise convert via Pal2PacE.exe (timeout=120s for 4K/8K textures) and save to cache.
            """
            if not src_img_path or not os.path.exists(src_img_path):
                return False
            if src_img_path.lower().endswith(".paa"):
                shutil.copy(src_img_path, dst_paa_path)
                return True
            is_ca_target = dst_paa_path.lower().endswith("_ca.paa")
            if not is_ca_target:
                sibling_paa = os.path.splitext(src_img_path)[0] + ".paa"
                if os.path.exists(sibling_paa) and os.path.getsize(sibling_paa) > 0:
                    shutil.copy(sibling_paa, dst_paa_path)
                    return True
            os.makedirs(THUMBS_DIR, exist_ok=True)
            st = os.stat(src_img_path)
            suf_tag = "ca" if is_ca_target else "co"
            h_key = hashlib.md5(f"{os.path.abspath(src_img_path)}:{st.st_size}:{int(st.st_mtime)}:{suf_tag}".encode("utf-8")).hexdigest()[:12]
            cached_paa = os.path.join(THUMBS_DIR, f"ezcache_v3_{h_key}_{suf_tag}.paa")
            if os.path.exists(cached_paa) and os.path.getsize(cached_paa) > 0:
                shutil.copy(cached_paa, dst_paa_path)
                return True
            src_to_convert = src_img_path
            if fallback_png_path:
                ext = os.path.splitext(src_img_path)[1].lower()
                temp_src = os.path.splitext(fallback_png_path)[0] + (ext if ext in (".png", ".tga", ".jpg", ".jpeg") else ".png")
                shutil.copy(src_img_path, temp_src)
                src_to_convert = temp_src
            subprocess.run([PAL2PAC_EXE, src_to_convert, dst_paa_path], cwd=PAL2PAC_DIR, capture_output=True, timeout=120)
            if os.path.exists(dst_paa_path) and os.path.getsize(dst_paa_path) > 0:
                shutil.copy(dst_paa_path, cached_paa)
                return True
            return False

        for idx, slot_entry in enumerate(material_slots):
            s_diff = slot_entry[0] if len(slot_entry) > 0 else None
            s_norm = slot_entry[1] if len(slot_entry) > 1 else None
            s_glass = slot_entry[2] if len(slot_entry) > 2 else None
            is_glass = bool(s_glass and s_glass.get("is_glass"))

            s_name = "camo" if idx == 0 else f"camo{idx}"
            slot_names.append(s_name)

            # 1. Convert Diffuse Texture -> <s_name>_ca.paa (for Glass) or <s_name>_co.paa (for opaque)
            paa_suf = "_ca.paa" if is_glass else "_co.paa"
            tex_filename = f"{s_name}{paa_suf}"
            slot_tex_files.append(tex_filename)
            paa_co_dst = os.path.join(dst_dir, "data", tex_filename)
            png_for_cache = os.path.join(work_dir, f"{s_name}{'_ca' if is_glass else '_co'}.png")

            if is_glass:
                clean_diff = apply_glass_tint_and_alpha_png(
                    s_diff,
                    output_dir=work_dir,
                    alpha=s_glass.get("alpha", 0.42),
                    tint_rgb=s_glass.get("tint_rgb"),
                )
            else:
                clean_diff = ensure_opaque_rgb_png(s_diff, output_dir=work_dir) if (s_diff and os.path.exists(s_diff)) else s_diff

            convert_or_copy_paa(clean_diff, paa_co_dst, fallback_png_path=png_for_cache)
            if not os.path.exists(paa_co_dst):
                create_solid_png(png_for_cache, rgb=(78, 88, 68), alpha=(110 if is_glass else 255))
                subprocess.run([PAL2PAC_EXE, png_for_cache, paa_co_dst], cwd=PAL2PAC_DIR, capture_output=True, timeout=60)

            if os.path.exists(paa_co_dst):
                shutil.copy(paa_co_dst, os.path.join(src_dir, "data", tex_filename))

            # 2. Convert Normal Map -> <s_name>_nohq.paa (optional)
            has_normal = False
            if s_norm and os.path.exists(s_norm):
                clean_norm = unpack_dxt5nm_if_needed(s_norm, output_dir=work_dir)
                paa_nohq_dst = os.path.join(dst_dir, "data", f"{s_name}_nohq.paa")
                if convert_or_copy_paa(clean_norm, paa_nohq_dst):
                    has_normal = True
                    shutil.copy(paa_nohq_dst, os.path.join(src_dir, "data", f"{s_name}_nohq.paa"))

            # 3. Generate RVMAT for this slot (reflective Fresnel glass vs standard material)
            rvmat_content = generate_rvmat(addon_name, has_normal=has_normal, slot_name=s_name, is_glass=is_glass)
            for d in (src_dir, dst_dir):
                with open(os.path.join(d, "data", f"{s_name}.rvmat"), "w", encoding="utf-8") as f:
                    f.write(rvmat_content)

            slot_paths.append((s_name, f"{addon_name}\\data\\{tex_filename}", f"{addon_name}\\data\\{s_name}.rvmat"))

        # 4. Write MLOD .p3d
        if empty_view_pilot is None:
            use_empty_pilot = (gear_type.lower() in ("headgear", "glasses", "goggles", "facewear"))
        else:
            use_empty_pilot = bool(empty_view_pilot)
        mlod_p3d_path = os.path.join(src_dir, f"{addon_name}.p3d")
        write_mlod_p3d(
            mlod_p3d_path,
            vertices,
            normals,
            faces,
            vertex_groups,
            tex_path=f"{addon_name}\\data\\camo_co.paa",
            rvmat_path=f"{addon_name}\\data\\camo.rvmat",
            slot_paths=slot_paths,
            empty_view_pilot=use_empty_pilot,
            include_soldier_proxies=(gear_type == "UNIFORM"),
        )

        # Save MLOD copy into .cache_thumbs so ArmaModManager 3D Inspector can preview it immediately!
        os.makedirs(THUMBS_DIR, exist_ok=True)
        shutil.copy(mlod_p3d_path, os.path.join(THUMBS_DIR, f"{addon_name}_mlod.p3d"))

        # 5. Write model.cfg & Binarize to ODOL .p3d
        with open(os.path.join(src_dir, "model.cfg"), "w", encoding="utf-8") as f:
            f.write(generate_model_cfg(addon_name, slot_names=slot_names))

        dst_p3d_path = os.path.join(dst_dir, f"{addon_name}.p3d")
        if os.path.exists(BINARIZE_EXE):
            src_root = os.path.dirname(src_dir)
            subprocess.run(
                [
                    BINARIZE_EXE,
                    "-silent",
                    "-always",
                    "-noLogs",
                    f"-binpath={BINARIZE_DIR}",
                    f"-textures={src_root}",
                    src_dir,
                    dst_dir,
                    f"{addon_name}.p3d",
                ],
                cwd=src_root,
                capture_output=True,
                timeout=120,
            )

        # Fallback to MLOD if binarize didn't output
        if not os.path.exists(dst_p3d_path) or os.path.getsize(dst_p3d_path) == 0:
            shutil.copy(mlod_p3d_path, dst_p3d_path)

        # 6. Generate config.cpp & compile to config.bin
        cpp_content = generate_config_cpp(
            addon_name=addon_name,
            class_name=class_name,
            display_name=display_name,
            author=author,
            gear_type=gear_type,
            armor=armor,
            mass=mass,
            max_load=max_load,
            slot_names=slot_names,
            aceax_cfg=aceax_cfg,
            slot_tex_files=slot_tex_files,
        )
        cpp_path = os.path.join(dst_dir, "config.cpp")
        bin_path = os.path.join(dst_dir, "config.bin")
        with open(cpp_path, "w", encoding="utf-8") as f:
            f.write(cpp_content)

        if os.path.exists(CFGCONVERT_EXE):
            subprocess.run([CFGCONVERT_EXE, "-bin", "-dst", bin_path, cpp_path], capture_output=True, timeout=10)
            if os.path.exists(bin_path) and os.path.getsize(bin_path) > 0:
                os.remove(cpp_path)

        # 7. Ensure target @Mod folder, mod.cpp & meta.cpp exist
        target_mod_dir, target_addons_dir, clean_mod_name = resolve_mod_dirs(mod_folder_name)
        ensure_mod_metadata(target_mod_dir, target_addons_dir, clean_mod_name, author=author)

        # 8. Pack PBO
        out_pbo = os.path.join(target_addons_dir, f"{addon_name}.pbo")
        pack_folder_to_pbo(dst_dir, out_pbo, addon_name)

        return {
            "ok": True,
            "addon_name": addon_name,
            "class_name": class_name,
            "display_name": display_name,
            "pbo_path": out_pbo,
            "mod_dir": target_mod_dir,
            "mod_name": clean_mod_name,
            "pbo_size": os.path.getsize(out_pbo),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _convert_or_copy_paa_cached(src_img_path, dst_paa_path, fallback_png_path=None):
    if not src_img_path or not os.path.exists(src_img_path):
        return False
    if src_img_path.lower().endswith(".paa"):
        shutil.copy(src_img_path, dst_paa_path)
        return True
    is_ca_target = dst_paa_path.lower().endswith("_ca.paa")
    if not is_ca_target:
        sibling_paa = os.path.splitext(src_img_path)[0] + ".paa"
        if os.path.exists(sibling_paa) and os.path.getsize(sibling_paa) > 0:
            shutil.copy(sibling_paa, dst_paa_path)
            return True
    os.makedirs(THUMBS_DIR, exist_ok=True)
    st = os.stat(src_img_path)
    suf_tag = "ca" if is_ca_target else "co"
    h_key = hashlib.md5(f"{os.path.abspath(src_img_path)}:{st.st_size}:{int(st.st_mtime)}:{suf_tag}".encode("utf-8")).hexdigest()[:12]
    cached_paa = os.path.join(THUMBS_DIR, f"ezcache_v3_{h_key}_{suf_tag}.paa")
    if os.path.exists(cached_paa) and os.path.getsize(cached_paa) > 0:
        shutil.copy(cached_paa, dst_paa_path)
        return True
    src_to_convert = src_img_path
    if fallback_png_path:
        ext = os.path.splitext(src_img_path)[1].lower()
        temp_src = os.path.splitext(fallback_png_path)[0] + (ext if ext in (".png", ".tga", ".jpg", ".jpeg") else ".png")
        shutil.copy(src_img_path, temp_src)
        src_to_convert = temp_src
    subprocess.run([PAL2PAC_EXE, src_to_convert, dst_paa_path], cwd=PAL2PAC_DIR, capture_output=True, timeout=120)
    if os.path.exists(dst_paa_path) and os.path.getsize(dst_paa_path) > 0:
        shutil.copy(dst_paa_path, cached_paa)
        return True
    return False


def generate_multi_config_cpp(
    addon_name,
    base_class_name,
    display_name,
    gear_type,
    variant_specs,
    author="EZ_MOD Creator",
    armor=15,
    mass=15,
    max_load=160,
    aceax_cfg=None,
):
    """Generates a single config.cpp with one shared XtdGearModels card and multiple variant classes."""
    desc = f"EZ_MOD Custom {gear_type} ({display_name})"
    xtd_root_block = ""
    req_aceax = ""
    m_id = "EZ_Custom_Pack"
    if aceax_cfg and aceax_cfg.get("enabled"):
        m_id = re.sub(r"[^a-zA-Z0-9_]", "_", aceax_cfg.get("model", "EZ_Custom_Pack")).strip("_") or "EZ_Custom_Pack"
        m_label = aceax_cfg.get("label", display_name).replace('"', "'")
        req_aceax = ', "aceax_gearinfo"'
        root_cat = "CfgVehicles" if gear_type == "BACKPACK" else ("CfgGlasses" if gear_type == "GOGGLES" else "CfgWeapons")
        opts = aceax_cfg.get("options") or []
        opt_ids = []
        opt_classes = []
        for opt in opts:
            oid = re.sub(r"[^a-zA-Z0-9_]", "_", str(opt.get("id", "Option"))).strip("_") or "Option"
            olabel = str(opt.get("label") or oid).replace('"', "'")
            vals = [str(v).replace('"', "'").strip() for v in (opt.get("values") or []) if str(v).strip()]
            if not vals:
                vals = ["Base"]
            opt_ids.append(f'"{oid}"')
            vals_str = ", ".join(f'"{v}"' for v in vals)
            opt_classes.append(
                f"""            class {oid} {{
                label = "{olabel}";
                alwaysSelectable = 1;
                values[] = {{{vals_str}}};
            }};"""
            )
        xtd_root_block = f"""
class XtdGearModels {{
    class {root_cat} {{
        class {m_id} {{
            label = "{m_label}";
            author = "{author}";
            options[] = {{{", ".join(opt_ids)}}};
{chr(10).join(opt_classes)}
        }};
    }};
}};
"""

    class_names = [v["class_name"] for v in variant_specs]
    cls_arr_str = ", ".join(f'"{c}"' for c in class_names)
    if gear_type == "UNIFORM":
        units_str = ", ".join(f'"{c}_Soldier"' for c in class_names)
        weapons_str = cls_arr_str
    else:
        units_str = cls_arr_str if gear_type == "BACKPACK" else ""
        weapons_str = cls_arr_str if gear_type in ("HEADGEAR", "VEST", "NVG") else ""

    item_blocks = []
    vehicle_blocks = []
    for vs in variant_specs:
        cname = vs["class_name"]
        model_p3d = f"\\{addon_name}\\{vs['p3d_name']}"
        s_names = vs["slot_names"]
        tex_paths = vs["tex_paths"]
        hs_str = ", ".join(f'"{s}"' for s in s_names)
        hst_str = ", ".join(f'"\\{tp}"' for tp in tex_paths)

        xtd_item_block = ""
        if aceax_cfg and aceax_cfg.get("enabled"):
            sel_map = vs.get("aceax_selections") or {}
            lines = [f'            model = "{m_id}";']
            for k, val in sel_map.items():
                oid = re.sub(r"[^a-zA-Z0-9_]", "_", str(k)).strip("_") or "Option"
                sval = str(val).replace('"', "'").strip()
                lines.append(f'            {oid} = "{sval}";')
            xtd_item_block = "\n        class XtdGearInfo {\n" + "\n".join(lines) + "\n        };"

        if gear_type == "HEADGEAR":
            item_blocks.append(f"""    class {cname}: ItemCore {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: HeadgearItem {{
            mass = {int(mass)};
            uniformModel = "{model_p3d}";
            modelSides[] = {{3, 1, 2, 0}};
            hiddenSelections[] = {{{hs_str}}};
            class HitpointsProtectionInfo {{
                class Head {{
                    hitpointName = "HitHead";
                    armor = {int(armor)};
                    passThrough = 0.5;
                }};
            }};
        }};
    }};""")
        elif gear_type == "VEST":
            item_blocks.append(f"""    class {cname}: Vest_Camo_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: VestItem {{
            uniformModel = "{model_p3d}";
            containerClass = "Supply{int(max_load)}";
            mass = {int(mass)};
            hiddenSelections[] = {{{hs_str}}};
            class HitpointsProtectionInfo {{
                class Chest {{
                    hitpointName = "HitChest";
                    armor = {int(armor)};
                    passThrough = 0.3;
                }};
                class Diaphragm {{
                    hitpointName = "HitDiaphragm";
                    armor = {int(armor)};
                    passThrough = 0.3;
                }};
                class Abdomen {{
                    hitpointName = "HitAbdomen";
                    armor = {int(armor)};
                    passThrough = 0.3;
                }};
                class Body {{
                    hitpointName = "HitBody";
                    passThrough = 0.3;
                }};
            }};
        }};
    }};""")
        elif gear_type == "UNIFORM":
            soldier_class = f"{cname}_Soldier"
            vehicle_blocks.append(f"""    class {soldier_class}: B_Soldier_base_F {{
        scope = 1;
        author = "{author}";
        displayName = "{display_name}";
        uniformClass = "{cname}";
        model = "{model_p3d}";
        modelSides[] = {{3, 1, 2, 0}};
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
    }};""")
            item_blocks.append(f"""    class {cname}: Uniform_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "\\A3\\Characters_F\\Common\\Suitpacks\\suitpack_universal_F.p3d";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo: UniformItem {{
            uniformModel = "-";
            uniformClass = "{soldier_class}";
            containerClass = "Supply{min(200, max(20, int(max_load)))}";
            mass = {int(mass)};
        }};
    }};""")
        elif gear_type == "BACKPACK":
            item_blocks.append(f"""    class {cname}: B_AssaultPack_Base {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
        maximumLoad = {int(max_load)};
        mass = {int(mass)};{xtd_item_block}
    }};""")
        elif gear_type == "GOGGLES":
            item_blocks.append(f"""    class {cname}: None {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};
        mass = {int(mass)};{xtd_item_block}
    }};""")
        else:  # NVG
            item_blocks.append(f"""    class {cname}: NVGoggles {{
        scope = 2;
        scopeArsenal = 2;
        author = "{author}";
        displayName = "{display_name}";
        descriptionShort = "{desc}";
        model = "{model_p3d}";
        visionMode[] = {{"Normal"}};
        hiddenSelections[] = {{{hs_str}}};
        hiddenSelectionsTextures[] = {{{hst_str}}};{xtd_item_block}
        class ItemInfo {{
            type = 616;
            hmdType = 0;
            uniformModel = "{model_p3d}";
            modelOff = "{model_p3d}";
            mass = {int(mass)};
            hiddenSelections[] = {{{hs_str}}};
        }};
    }};""")

    if gear_type == "HEADGEAR":
        container_block = "class CfgWeapons {\n    class ItemCore;\n    class HeadgearItem;\n" + "\n".join(item_blocks) + "\n};"
    elif gear_type == "VEST":
        container_block = "class CfgWeapons {\n    class Vest_Camo_Base;\n    class VestItem;\n" + "\n".join(item_blocks) + "\n};"
    elif gear_type == "UNIFORM":
        container_block = (
            "class CfgVehicles {\n    class B_Soldier_base_F;\n"
            + "\n".join(vehicle_blocks)
            + "\n};\nclass CfgWeapons {\n    class Uniform_Base;\n    class UniformItem;\n"
            + "\n".join(item_blocks)
            + "\n};"
        )
    elif gear_type == "BACKPACK":
        container_block = "class CfgVehicles {\n    class B_AssaultPack_Base;\n" + "\n".join(item_blocks) + "\n};"
    elif gear_type == "GOGGLES":
        container_block = "class CfgGlasses {\n    class None;\n" + "\n".join(item_blocks) + "\n};"
    else:
        container_block = "class CfgWeapons {\n    class NVGoggles;\n" + "\n".join(item_blocks) + "\n};"

    return f"""class CfgPatches {{
    class {addon_name} {{
        units[] = {{{units_str}}};
        weapons[] = {{{weapons_str}}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"A3_Characters_F"{req_aceax}}};
    }};
}};
{xtd_root_block}
{container_block}
"""


def build_ez_mod_variants(
    slug,
    display_name,
    gear_type,
    variants,
    author="EZ_MOD Creator",
    armor=15,
    mass=15,
    max_load=160,
    aceax_cfg=None,
    mod_folder_name=None,
    empty_view_pilot=None,
):
    """Builds a single PBO containing multiple variant .p3d models under one XtdGearModels card."""
    if not variants:
        raise ValueError("variants list is empty")
    if len(variants) == 1 or gear_type == "FACE":
        v0 = variants[0]
        return build_ez_mod(
            slug=slug,
            display_name=display_name,
            gear_type=gear_type,
            vertices=v0["vertices"],
            normals=v0["normals"],
            faces=v0["faces"],
            vertex_groups=v0["vertex_groups"],
            author=author,
            armor=armor,
            mass=mass,
            max_load=max_load,
            material_slots=v0.get("material_slots"),
            aceax_cfg=aceax_cfg,
            mod_folder_name=mod_folder_name,
            empty_view_pilot=empty_view_pilot,
        )

    clean_slug = re.sub(r"[^a-zA-Z0-9_]", "_", slug).lower().strip("_") or "custom_item"
    addon_name = f"ez_{clean_slug}"
    base_class_name = f"EZ_{clean_slug.upper()}"

    work_dir = tempfile.mkdtemp(prefix=f"ezmod_{addon_name}_")
    src_dir = os.path.join(work_dir, "src", addon_name)
    dst_dir = os.path.join(work_dir, "dst", addon_name)
    os.makedirs(os.path.join(src_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "data"), exist_ok=True)

    try:
        tex_cache = {}  # (diff_sig, norm_sig) -> (co_rel, rvmat_rel)
        tex_counter = 0
        all_slot_names = []
        model_names = []
        variant_specs = []

        def file_sig(p):
            if not p or not os.path.exists(p):
                return ""
            st = os.stat(p)
            return f"{os.path.abspath(p)}:{st.st_size}:{int(st.st_mtime)}"

        for vi, var in enumerate(variants):
            mat_slots = var.get("material_slots") or [(None, None)]
            slot_paths = []
            slot_names = []
            tex_paths = []
            for idx, slot_entry in enumerate(mat_slots):
                s_diff = slot_entry[0] if len(slot_entry) > 0 else None
                s_norm = slot_entry[1] if len(slot_entry) > 1 else None
                s_glass = slot_entry[2] if len(slot_entry) > 2 else None
                is_glass = bool(s_glass and s_glass.get("is_glass"))

                s_name = "camo" if idx == 0 else f"camo{idx}"
                slot_names.append(s_name)
                if s_name not in all_slot_names:
                    all_slot_names.append(s_name)

                sig = (file_sig(s_diff), file_sig(s_norm), str(s_glass) if is_glass else "")
                if sig in tex_cache:
                    co_rel, rv_rel = tex_cache[sig]
                else:
                    base_tok = s_name if tex_counter == idx and vi == 0 else f"tex_{tex_counter}"
                    tex_counter += 1
                    paa_suf = "_ca.paa" if is_glass else "_co.paa"
                    tex_fn = f"{base_tok}{paa_suf}"
                    paa_co_dst = os.path.join(dst_dir, "data", tex_fn)
                    png_tmp = os.path.join(work_dir, f"{base_tok}{'_ca' if is_glass else '_co'}.png")
                    if is_glass:
                        clean_diff = apply_glass_tint_and_alpha_png(
                            s_diff,
                            output_dir=work_dir,
                            alpha=s_glass.get("alpha", 0.42),
                            tint_rgb=s_glass.get("tint_rgb"),
                        )
                    else:
                        clean_diff = ensure_opaque_rgb_png(s_diff, output_dir=work_dir) if (s_diff and os.path.exists(s_diff)) else s_diff
                    _convert_or_copy_paa_cached(clean_diff, paa_co_dst, fallback_png_path=png_tmp)
                    if not os.path.exists(paa_co_dst):
                        create_solid_png(png_tmp, rgb=(78, 88, 68), alpha=(110 if is_glass else 255))
                        subprocess.run([PAL2PAC_EXE, png_tmp, paa_co_dst], cwd=PAL2PAC_DIR, capture_output=True, timeout=60)
                    if os.path.exists(paa_co_dst):
                        shutil.copy(paa_co_dst, os.path.join(src_dir, "data", tex_fn))

                    has_normal = False
                    if s_norm and os.path.exists(s_norm):
                        clean_norm = unpack_dxt5nm_if_needed(s_norm, output_dir=work_dir)
                        paa_nohq_dst = os.path.join(dst_dir, "data", f"{base_tok}_nohq.paa")
                        if _convert_or_copy_paa_cached(clean_norm, paa_nohq_dst):
                            has_normal = True
                            shutil.copy(paa_nohq_dst, os.path.join(src_dir, "data", f"{base_tok}_nohq.paa"))

                    rvmat_content = generate_rvmat(addon_name, has_normal=has_normal, slot_name=base_tok, is_glass=is_glass)
                    for d in (src_dir, dst_dir):
                        with open(os.path.join(d, "data", f"{base_tok}.rvmat"), "w", encoding="utf-8") as f:
                            f.write(rvmat_content)
                    co_rel = f"{addon_name}\\data\\{tex_fn}"
                    rv_rel = f"{addon_name}\\data\\{base_tok}.rvmat"
                    tex_cache[sig] = (co_rel, rv_rel)

                slot_paths.append((s_name, co_rel, rv_rel))
                tex_paths.append(co_rel)

            m_name = addon_name if vi == 0 else f"{addon_name}_v{vi}"
            c_name = base_class_name if vi == 0 else f"{base_class_name}_V{vi}"
            model_names.append(m_name)
            mlod_p3d = os.path.join(src_dir, f"{m_name}.p3d")
            if empty_view_pilot is None:
                is_head_slot = (gear_type.lower() in ("headgear", "glasses", "goggles", "facewear"))
            else:
                is_head_slot = bool(empty_view_pilot)
            write_mlod_p3d(
                mlod_p3d,
                var["vertices"],
                var["normals"],
                var["faces"],
                var["vertex_groups"],
                tex_path=tex_paths[0],
                rvmat_path=slot_paths[0][2],
                slot_paths=slot_paths,
                empty_view_pilot=is_head_slot,
                include_soldier_proxies=(gear_type == "UNIFORM"),
            )
            if vi == 0:
                os.makedirs(THUMBS_DIR, exist_ok=True)
                shutil.copy(mlod_p3d, os.path.join(THUMBS_DIR, f"{addon_name}_mlod.p3d"))

            variant_specs.append({
                "class_name": c_name,
                "p3d_name": f"{m_name}.p3d",
                "slot_names": slot_names,
                "tex_paths": tex_paths,
                "aceax_selections": var.get("aceax_selections") or {},
            })

        with open(os.path.join(src_dir, "model.cfg"), "w", encoding="utf-8") as f:
            f.write(generate_model_cfg(model_names[0], slot_names=all_slot_names, extra_models=model_names[1:]))

        for m_name in model_names:
            dst_p3d = os.path.join(dst_dir, f"{m_name}.p3d")
            if os.path.exists(BINARIZE_EXE):
                src_root = os.path.dirname(src_dir)
                subprocess.run(
                    [
                        BINARIZE_EXE,
                        "-silent",
                        "-always",
                        "-noLogs",
                        f"-binpath={BINARIZE_DIR}",
                        f"-textures={src_root}",
                        src_dir,
                        dst_dir,
                        f"{m_name}.p3d",
                    ],
                    cwd=src_root,
                    capture_output=True,
                    timeout=120,
                )
            if not os.path.exists(dst_p3d) or os.path.getsize(dst_p3d) == 0:
                shutil.copy(os.path.join(src_dir, f"{m_name}.p3d"), dst_p3d)

        cpp_content = generate_multi_config_cpp(
            addon_name=addon_name,
            base_class_name=base_class_name,
            display_name=display_name,
            gear_type=gear_type,
            variant_specs=variant_specs,
            author=author,
            armor=armor,
            mass=mass,
            max_load=max_load,
            aceax_cfg=aceax_cfg,
        )
        cpp_path = os.path.join(dst_dir, "config.cpp")
        bin_path = os.path.join(dst_dir, "config.bin")
        with open(cpp_path, "w", encoding="utf-8") as f:
            f.write(cpp_content)

        if os.path.exists(CFGCONVERT_EXE):
            subprocess.run([CFGCONVERT_EXE, "-bin", "-dst", bin_path, cpp_path], capture_output=True, timeout=10)
            if os.path.exists(bin_path) and os.path.getsize(bin_path) > 0:
                os.remove(cpp_path)

        target_mod_dir, target_addons_dir, clean_mod_name = resolve_mod_dirs(mod_folder_name)
        ensure_mod_metadata(target_mod_dir, target_addons_dir, clean_mod_name, author=author)

        out_pbo = os.path.join(target_addons_dir, f"{addon_name}.pbo")
        pack_folder_to_pbo(dst_dir, out_pbo, addon_name)

        return {
            "ok": True,
            "addon_name": addon_name,
            "class_name": base_class_name,
            "display_name": display_name,
            "pbo_path": out_pbo,
            "mod_dir": target_mod_dir,
            "mod_name": clean_mod_name,
            "pbo_size": os.path.getsize(out_pbo),
            "variant_count": len(variant_specs),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ==============================================================================
# BLENDER 1-CLICK IMPORT BRIDGE (PBO / ODOL / MLOD + CONFIG.BIN + PAA -> BLENDER)
# ==============================================================================

BRIDGE_DIR = os.path.join(APP_DIR, "extracted", "blender_bridge")
BRIDGE_TEX_DIR = os.path.join(BRIDGE_DIR, "textures")
BRIDGE_MANIFEST = os.path.join(BRIDGE_DIR, "latest_import.json")
BRIDGE_HISTORY = os.path.join(BRIDGE_DIR, "history.json")
DEBINARIZER_EXE = os.path.join(TOOLS_DIR, "P3DDebinarizer.exe")


def _read_cstr(f):
    chars = []
    while True:
        c = f.read(1)
        if not c or c == b"\x00":
            break
        chars.append(c)
    return b"".join(chars).decode("latin1", errors="replace")


def read_pbo_index(pbo_path):
    """Returns (prefix, entries_list, data_start_offset) for a PBO archive."""
    if not pbo_path or not os.path.exists(pbo_path):
        return "", [], 0
    with open(pbo_path, "rb") as f:
        _read_cstr(f)
        f.read(20)
        prefix = ""
        while True:
            k = _read_cstr(f)
            if not k:
                break
            v = _read_cstr(f)
            if k.lower() == "prefix":
                prefix = v.replace("\\", "/").strip("/")
        entries = []
        while True:
            fn = _read_cstr(f)
            buf = f.read(20)
            if len(buf) < 20:
                break
            magic, orig_sz, res, ts, data_sz = struct.unpack("<IIIII", buf)
            if not fn and data_sz == 0:
                break
            entries.append((fn, data_sz))
        data_offset = f.tell()
    return prefix, entries, data_offset


def extract_from_pbo_by_path(pbo_path, target_fn, strict_prefix=False):
    if not pbo_path or not os.path.exists(pbo_path) or not target_fn:
        return None
    clean_t = target_fn.replace("\\", "/").lower().lstrip("/")
    prefix, entries, cur_off = read_pbo_index(pbo_path)
    pref_low = prefix.lower()
    rel_t = clean_t
    if pref_low and clean_t.startswith(pref_low + "/"):
        rel_t = clean_t[len(pref_low) + 1 :]

    with open(pbo_path, "rb") as f:
        f.seek(cur_off)
        for fn, sz in entries:
            cfn = fn.replace("\\", "/").lower().lstrip("/")
            if cfn == clean_t or cfn == rel_t:
                return f.read(sz)
            if not strict_prefix and (cfn.endswith("/" + clean_t) or clean_t.endswith("/" + cfn)):
                return f.read(sz)
            f.seek(sz, os.SEEK_CUR)
    return None


def extract_across_mod_pbos(pbo_path, target_fn):
    """Finds and extracts a file from pbo_path or any sibling .pbo in the same @Mod/addons folder."""
    if not pbo_path or not target_fn:
        return None
    clean_t = target_fn.replace("\\", "/").lstrip("/")
    parts = clean_t.split("/")
    addons_dir = os.path.dirname(pbo_path)
    if len(parts) > 1 and os.path.isdir(addons_dir):
        first_seg = parts[0].lower()
        pbo_stem = os.path.splitext(os.path.basename(pbo_path))[0].lower()
        if first_seg != pbo_stem:
            for f in os.listdir(addons_dir):
                if f.lower() == first_seg + ".pbo":
                    sib = os.path.join(addons_dir, f)
                    b = extract_from_pbo_by_path(sib, clean_t, strict_prefix=True) or extract_from_pbo_by_path(
                        sib, "/".join(parts[1:]), strict_prefix=True
                    )
                    if b:
                        return b
    b = extract_from_pbo_by_path(pbo_path, clean_t, strict_prefix=True) or extract_from_pbo_by_path(
        pbo_path, clean_t, strict_prefix=False
    )
    if b:
        return b
    if len(parts) > 1 and os.path.isdir(addons_dir):
        for f in os.listdir(addons_dir):
            if f.lower().endswith(".pbo") and os.path.join(addons_dir, f) != pbo_path:
                sib = os.path.join(addons_dir, f)
                b = extract_from_pbo_by_path(sib, clean_t, strict_prefix=True)
                if b:
                    return b
    return None


def decompile_config_bytes_to_text(cfg_bytes):
    """Converts binary Arma 3 config.bin (raP) or text config.cpp bytes into readable C++ text."""
    if not cfg_bytes:
        return ""
    if cfg_bytes[:4] != b"\x00raP":
        return cfg_bytes.decode("utf-8", errors="ignore")

    os.makedirs(THUMBS_DIR, exist_ok=True)
    h = hashlib.md5(cfg_bytes).hexdigest()[:12]
    cached_cpp = os.path.join(THUMBS_DIR, f"cfg_{h}.cpp")
    if os.path.exists(cached_cpp) and os.path.getsize(cached_cpp) > 0:
        with open(cached_cpp, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    if os.path.exists(CFGCONVERT_EXE):
        tmp_bin = os.path.join(THUMBS_DIR, f"cfg_{h}.bin")
        with open(tmp_bin, "wb") as f:
            f.write(cfg_bytes)
        try:
            subprocess.run([CFGCONVERT_EXE, "-txt", "-dst", cached_cpp, tmp_bin], capture_output=True, timeout=15)
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_bin):
                try:
                    os.remove(tmp_bin)
                except Exception:
                    pass
        if os.path.exists(cached_cpp) and os.path.getsize(cached_cpp) > 0:
            with open(cached_cpp, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    return ""


def guess_gear_type_from_hints(parent_cls="", item_info_parent="", model_path="", pbo_name="", class_name="", has_max_load=False):
    """Determines EZ_MOD gear_type enum ('HEADGEAR', 'VEST', 'UNIFORM', 'BACKPACK', 'GOGGLES', 'NVG')."""
    combined = f"{parent_cls} {item_info_parent} {model_path} {pbo_name} {class_name}".lower()
    if "b_soldier_base_f" in combined or "uniformitem" in combined or "uniform_base" in combined or "_soldier" in combined or "uniform" in combined or "combatuniform" in combined or "ghillie" in combined or "suit" in combined or "jacket" in combined or "pants" in combined:
        return "UNIFORM"
    if "nvg" in combined or "nightvision" in combined or "pvs" in combined or "gpnvg" in combined:
        return "NVG"
    if "headgearitem" in combined or "h_helmet" in combined or "helmet" in combined or "headgear" in combined or "hat" in combined or "cap" in combined or "beret" in combined or "boonie" in combined:
        return "HEADGEAR"
    if "vestitem" in combined or "vest_" in combined or "platecarrier" in combined or "vest" in combined or "carrier" in combined or "chest" in combined or "rig" in combined:
        return "VEST"
    if "cfgglasses" in combined or "g_combat" in combined or "glasses" in combined or "goggles" in combined or "balaclava" in combined or "mask" in combined or "shemag" in combined or "scarf" in combined or "facewear" in combined:
        return "GOGGLES"
    if "bag_base" in combined or "b_assaultpack" in combined or "backpack" in combined or "bag" in combined or "pack" in combined or "rucksack" in combined:
        return "BACKPACK"
    if has_max_load:
        return "BACKPACK"
    return "HEADGEAR"


def parse_config_cpp_classes(cpp_text, pbo_name=""):
    """
    Parses decompiled config.cpp text into a dictionary of classes with resolved inheritance,
    and builds a model_basename_lower -> config_metadata lookup.
    """
    if not cpp_text:
        return {}, {}

    # Find top-level and nested class blocks inside CfgWeapons / CfgVehicles / CfgGlasses
    # Simple brace-balanced scanner for `class <Name>[: <Parent>] { ... }`
    classes_raw = {}
    pattern = re.compile(r"\bclass\s+([a-zA-Z0-9_]+)\s*(?::\s*([a-zA-Z0-9_]+))?\s*\{")
    pos = 0
    n = len(cpp_text)

    while pos < n:
        m = pattern.search(cpp_text, pos)
        if not m:
            break
        cls_name = m.group(1)
        parent_name = m.group(2) or ""
        brace_start = m.end() - 1

        depth = 0
        i = brace_start
        while i < n:
            ch = cpp_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1

        body = cpp_text[brace_start + 1 : i]
        # Skip container classes like CfgPatches, CfgWeapons, CfgVehicles, CfgGlasses, ItemInfo, HitpointsProtectionInfo
        if cls_name.lower() in (
            "cfgpatches",
            "cfgweapons",
            "cfgvehicles",
            "cfgglasses",
            "cfgmods",
            "cfgmodels",
            "cfgskeletons",
            "xtdgearmodels",
            "xtdgearinfos",
        ):
            pos = m.end()
            continue

        if cls_name.lower() in ("iteminfo", "hitpointsprotectioninfo", "head", "body", "chest", "abdomen", "diaphragm", "pelvis", "neck", "arms", "hands", "legs"):
            pos = i + 1
            continue

        info = {"class_name": cls_name, "parent": parent_name}

        m_dn = re.search(r'displayName\s*=\s*"([^"]+)"', body, re.I)
        if m_dn:
            info["displayName"] = m_dn.group(1).strip()

        m_auth = re.search(r'author\s*=\s*"([^"]+)"', body, re.I)
        if m_auth:
            info["author"] = m_auth.group(1).strip()

        m_desc = re.search(r'descriptionShort\s*=\s*"([^"]+)"', body, re.I)
        if m_desc:
            info["description"] = m_desc.group(1).strip()

        m_pic = re.search(r'picture\s*=\s*"([^"]+)"', body, re.I)
        if m_pic:
            info["picture"] = m_pic.group(1).strip()

        m_mod = re.search(r'model\s*=\s*"([^"]+\.p3d)"', body, re.I) or re.search(r'uniformModel\s*=\s*"([^"]+\.p3d)"', body, re.I)
        if m_mod:
            info["model"] = m_mod.group(1).strip()

        m_iinfo = re.search(r"class\s+ItemInfo\s*:\s*([a-zA-Z0-9_]+)", body, re.I)
        if m_iinfo:
            info["item_info_parent"] = m_iinfo.group(1).strip()

        m_mass = re.search(r"\bmass\s*=\s*([0-9]+(?:\.[0-9]+)?)", body, re.I)
        if m_mass:
            info["mass"] = int(round(float(m_mass.group(1))))

        m_load = re.search(r"\bmaximumLoad\s*=\s*([0-9]+(?:\.[0-9]+)?)", body, re.I)
        if m_load:
            info["maximumLoad"] = int(round(float(m_load.group(1))))

        # Find highest armor value in HitpointsProtectionInfo
        armors = [float(x) for x in re.findall(r"\barmor\s*=\s*([0-9]+(?:\.[0-9]+)?)", body, re.I)]
        if armors:
            info["armor"] = int(round(max(armors)))

        # hiddenSelections[] = { "camo", "camo1" };
        m_hs = re.search(r"hiddenSelections\[\]\s*=\s*\{([^}]*)\}", body, re.I)
        if m_hs:
            info["hiddenSelections"] = [s.strip() for s in re.findall(r'"([^"]+)"', m_hs.group(1))]

        # hiddenSelectionsTextures[] = { "...", "..." };
        m_hst = re.search(r"hiddenSelectionsTextures\[\]\s*=\s*\{([^}]*)\}", body, re.I)
        if m_hst:
            info["hiddenSelectionsTextures"] = [s.strip() for s in re.findall(r'"([^"]+)"', m_hst.group(1))]

        # Only store if it has interesting fields or inherits
        if len(info) > 2:
            existing = classes_raw.get(cls_name, {})
            existing.update(info)
            classes_raw[cls_name] = existing

        # Advance past header so inner classes or sibling classes are scanned
        pos = i + 1

    # Resolve inheritance chain (up to 6 levels)
    resolved_classes = {}
    for cname in classes_raw:
        chain = []
        cur = cname
        seen = set()
        while cur and cur in classes_raw and cur not in seen and len(chain) < 8:
            seen.add(cur)
            chain.append(classes_raw[cur])
            cur = classes_raw[cur].get("parent", "")
        merged = {}
        for item in reversed(chain):
            merged.update(item)
        merged["class_name"] = cname
        merged["root_parent"] = chain[-1].get("parent", "") if chain else ""
        resolved_classes[cname] = merged

    by_model = {}
    for cname, info in resolved_classes.items():
        mod_path = info.get("model", "")
        if not mod_path:
            continue
        mod_fn = os.path.basename(mod_path.replace("\\", "/")).lower()
        entry = by_model.get(mod_fn)
        gtype = guess_gear_type_from_hints(
            parent_cls=info.get("root_parent", "") + " " + info.get("parent", ""),
            item_info_parent=info.get("item_info_parent", ""),
            model_path=mod_path,
            pbo_name=pbo_name,
            class_name=cname,
            has_max_load=("maximumLoad" in info and "item_info_parent" not in info),
        )
        info["gear_type"] = gtype

        if not entry:
            by_model[mod_fn] = {
                "class_name": cname,
                "displayName": info.get("displayName", os.path.splitext(mod_fn)[0]),
                "description": info.get("description", ""),
                "picture": info.get("picture", ""),
                "author": info.get("author", "Arma 3 Mod"),
                "full_model_path": mod_path,
                "gear_type": gtype,
                "mass": info.get("mass", 15),
                "armor": info.get("armor", 6 if gtype == "HEADGEAR" else (20 if gtype == "VEST" else 0)),
                "maximumLoad": info.get("maximumLoad", 160 if gtype == "VEST" else (280 if gtype == "BACKPACK" else 0)),
                "hiddenSelections": info.get("hiddenSelections", ["camo"]),
                "hiddenSelectionsTextures": info.get("hiddenSelectionsTextures", []),
                "texture": (info.get("hiddenSelectionsTextures") or [""])[0],
                "variants": [
                    {
                        "class_name": cname,
                        "displayName": info.get("displayName", cname),
                        "textures": info.get("hiddenSelectionsTextures", []),
                    }
                ],
            }
        else:
            # Prefer a class that actually has a displayName and hiddenSelectionsTextures as primary
            if (not entry.get("displayName") or entry["displayName"] == os.path.splitext(mod_fn)[0]) and info.get("displayName"):
                entry["class_name"] = cname
                entry["displayName"] = info["displayName"]
                if info.get("description"):
                    entry["description"] = info["description"]
                if info.get("author"):
                    entry["author"] = info["author"]
            if not entry.get("hiddenSelectionsTextures") and info.get("hiddenSelectionsTextures"):
                entry["hiddenSelectionsTextures"] = info["hiddenSelectionsTextures"]
                entry["texture"] = info["hiddenSelectionsTextures"][0]
            if info.get("hiddenSelectionsTextures"):
                entry["variants"].append(
                    {
                        "class_name": cname,
                        "displayName": info.get("displayName", cname),
                        "textures": info.get("hiddenSelectionsTextures", []),
                    }
                )

    return by_model, resolved_classes


def parse_full_mlod_for_blender(filepath):
    """
    Reads an MLOD .p3d file and extracts complete Blender-ready geometry, normals,
    per-face UVs, per-face material slots, bone vertex weights, mass, and View Pilot status.
    """
    with open(filepath, "rb") as f:
        data = f.read()

    if data[:4] != b"MLOD":
        raise RuntimeError("Файл не является MLOD P3D: " + filepath)

    version, num_lods = struct.unpack("<II", data[4:12])
    pos = 12

    best_lod = None
    has_empty_view_pilot = False
    visual_lod_count = 0
    total_mass = 0.0

    for lod_idx in range(num_lods):
        if pos + 28 > len(data):
            break
        if data[pos : pos + 4] != b"P3DM":
            break
        major, minor, num_pts, num_nrm, num_faces, dummy = struct.unpack("<IIIIII", data[pos + 4 : pos + 28])
        pos += 28

        verts_bl = []
        for _ in range(num_pts):
            x, y, z, _ = struct.unpack("<fffi", data[pos : pos + 16])
            pos += 16
            verts_bl.append((float(x), float(z), float(y)))

        nrms_bl = []
        for _ in range(num_nrm):
            nx, ny, nz = struct.unpack("<fff", data[pos : pos + 12])
            pos += 12
            nrms_bl.append((-float(nx), -float(nz), -float(ny)))

        faces = []
        face_uvs = []
        face_nrms = []
        face_tex_mat = []

        for _ in range(num_faces):
            sides = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4
            f_v = []
            f_uv = []
            f_n = []
            for n in range(4):
                vi, ni, u, v = struct.unpack("<iiff", data[pos : pos + 16])
                pos += 16
                if n < sides:
                    f_v.append(int(vi))
                    f_uv.append((float(u), 1.0 - float(v)))
                    f_n.append(nrms_bl[ni] if 0 <= ni < len(nrms_bl) else (0.0, 0.0, 1.0))
            pos += 4  # flags
            t_end = data.find(b"\x00", pos)
            tex_str = data[pos:t_end].decode("latin1", errors="ignore").strip()
            pos = t_end + 1
            m_end = data.find(b"\x00", pos)
            mat_str = data[pos:m_end].decode("latin1", errors="ignore").strip()
            pos = m_end + 1

            if sides >= 3 and len(set(f_v)) == sides:
                faces.append(f_v)
                face_uvs.append(f_uv)
                face_nrms.append(f_n)
                face_tex_mat.append((tex_str, mat_str))

        selections = {}
        face_camo_slot = {}
        proxy_face_set = set()
        lod_mass = 0.0

        if pos + 4 <= len(data) and data[pos : pos + 4] == b"TAGG":
            pos += 4
            while pos + 6 <= len(data):
                act = data[pos]
                pos += 1
                t_end = data.find(b"\x00", pos)
                if t_end == -1:
                    break
                tname = data[pos:t_end].decode("latin1", errors="replace")
                pos = t_end + 1
                tsz = struct.unpack("<I", data[pos : pos + 4])[0]
                pos += 4
                if tname == "#EndOfFile#":
                    break
                elif tname == "#Mass#":
                    for _ in range(num_pts):
                        if pos + 4 <= len(data):
                            lod_mass += struct.unpack("<f", data[pos : pos + 4])[0]
                            pos += 4
                elif tname.startswith("proxy:") and tsz >= num_pts + len(faces):
                    f_bytes = data[pos + num_pts : pos + num_pts + len(faces)]
                    for fi, fb in enumerate(f_bytes):
                        if fb > 0:
                            proxy_face_set.add(fi)
                    pos += tsz
                elif not tname.startswith("#") and not tname.startswith("proxy:") and tsz >= num_pts:
                    pt_bytes = data[pos : pos + num_pts]
                    weights = {}
                    for i, bval in enumerate(pt_bytes):
                        if bval == 1:
                            weights[i] = 1.0
                        elif bval > 1:
                            weights[i] = round(max(0.01, (255 - bval) / 254.0), 4)
                    if weights:
                        selections[tname] = weights

                    tl = tname.lower()
                    if tl.startswith("camo") and tsz >= num_pts + len(faces):
                        f_bytes = data[pos + num_pts : pos + num_pts + len(faces)]
                        for fi, fb in enumerate(f_bytes):
                            if fb > 0 and fi not in face_camo_slot:
                                face_camo_slot[fi] = tl
                    pos += tsz
                else:
                    pos += tsz

        if proxy_face_set:
            # Strip proxy triangles from visual mesh so Blender never shows 2-meter proxy triangles
            clean_faces = []
            clean_uvs = []
            clean_nrms = []
            clean_tex_mat = []
            clean_camo = {}
            for fi in range(len(faces)):
                if fi in proxy_face_set:
                    continue
                new_fi = len(clean_faces)
                clean_faces.append(faces[fi])
                clean_uvs.append(face_uvs[fi])
                clean_nrms.append(face_nrms[fi])
                clean_tex_mat.append(face_tex_mat[fi])
                if fi in face_camo_slot:
                    clean_camo[new_fi] = face_camo_slot[fi]
            faces = clean_faces
            face_uvs = clean_uvs
            face_nrms = clean_nrms
            face_tex_mat = clean_tex_mat
            face_camo_slot = clean_camo

        res = 0.0
        if pos + 4 <= len(data):
            res = struct.unpack("<f", data[pos : pos + 4])[0]
            pos += 4

        if lod_mass > 0:
            total_mass = lod_mass

        if abs(res - 1100.0) < 5.0 and num_pts == 0 and num_faces == 0:
            has_empty_view_pilot = True

        if res < 900.0 and len(faces) > 0:
            visual_lod_count += 1
            if best_lod is None or res < best_lod["resolution"]:
                best_lod = {
                    "resolution": res,
                    "verts_bl": verts_bl,
                    "faces": faces,
                    "face_uvs": face_uvs,
                    "face_nrms": face_nrms,
                    "face_tex_mat": face_tex_mat,
                    "face_camo_slot": face_camo_slot,
                    "selections": selections,
                }

    if best_lod is None:
        raise RuntimeError("В P3D не найдено визуальных полигонов!")

    # Build distinct material slots by (camo_slot, tex_str, mat_str)
    slot_map = {}
    slot_list = []
    face_mat_indices = []

    for fi, (tex_s, mat_s) in enumerate(best_lod["face_tex_mat"]):
        c_slot = best_lod["face_camo_slot"].get(fi, "")
        if not c_slot and mat_s:
            c_slot = os.path.splitext(os.path.basename(mat_s.replace("\\", "/")))[0].lower()
        if not c_slot and tex_s:
            c_slot = re.sub(r"_(co|ca)$", "", os.path.splitext(os.path.basename(tex_s.replace("\\", "/")))[0], flags=re.I).lower()
        if not c_slot:
            c_slot = "camo"

        key = (c_slot, tex_s.lower(), mat_s.lower())
        if key not in slot_map:
            slot_map[key] = len(slot_list)
            slot_list.append(
                {
                    "slot_name": c_slot,
                    "tex_paa": tex_s,
                    "rvmat": mat_s,
                }
            )
        face_mat_indices.append(slot_map[key])

    return {
        "verts_bl": best_lod["verts_bl"],
        "faces": best_lod["faces"],
        "face_uvs": best_lod["face_uvs"],
        "face_nrms": best_lod["face_nrms"],
        "face_mat_indices": face_mat_indices,
        "material_slots": slot_list,
        "selections": best_lod["selections"],
        "mass": round(total_mass, 2),
        "has_empty_view_pilot": has_empty_view_pilot,
        "visual_lod_count": visual_lod_count,
    }


def _convert_paa_bytes_to_png_file(paa_bytes, out_dir, label_stem):
    if not paa_bytes or not os.path.exists(PAL2PAC_EXE):
        return ""
    os.makedirs(out_dir, exist_ok=True)
    h = hashlib.md5(paa_bytes).hexdigest()[:10]
    safe_stem = re.sub(r"[^a-zA-Z0-9_]", "_", label_stem)
    png_path = os.path.join(out_dir, f"{safe_stem}_{h}.png")
    if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
        return png_path
    paa_tmp = os.path.join(out_dir, f"{safe_stem}_{h}.paa")
    with open(paa_tmp, "wb") as f:
        f.write(paa_bytes)
    try:
        subprocess.run([PAL2PAC_EXE, paa_tmp, png_path], cwd=PAL2PAC_DIR, capture_output=True, timeout=20)
    except Exception:
        pass
    finally:
        if os.path.exists(paa_tmp):
            try:
                os.remove(paa_tmp)
            except Exception:
                pass
    return png_path if (os.path.exists(png_path) and os.path.getsize(png_path) > 0) else ""


def prepare_model_for_blender_import(pbo_path="", model_file="", skin_override=None):
    """
    Prepares a complete 1-click Blender import package (MLOD .p3d + converted PNG textures +
    parsed config.bin settings) and writes it to BRIDGE_MANIFEST (`latest_import.json`).
    Works both for models inside a PBO and standalone .p3d files on disk.
    """
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    os.makedirs(BRIDGE_TEX_DIR, exist_ok=True)

    p3d_bytes = None
    if pbo_path and os.path.exists(pbo_path):
        p3d_bytes = extract_from_pbo_by_path(pbo_path, model_file)
    elif model_file and os.path.exists(model_file):
        with open(model_file, "rb") as f:
            p3d_bytes = f.read()
        # Check if standalone file lives next to or came from a known PBO
        local_dir = os.path.dirname(model_file)
        for cand in os.listdir(local_dir) if os.path.isdir(local_dir) else []:
            if cand.lower().endswith(".pbo"):
                pbo_path = os.path.join(local_dir, cand)
                break

    if not p3d_bytes or len(p3d_bytes) < 4:
        raise RuntimeError(f"Не удалось прочитать модель: {model_file}")

    model_base = os.path.basename(model_file.replace("\\", "/"))
    model_stem = re.sub(r"[^a-zA-Z0-9_]", "_", os.path.splitext(model_base)[0])

    # 1. Ensure MLOD .p3d
    sig = p3d_bytes[:4]
    mlod_out_path = os.path.join(BRIDGE_DIR, f"{model_stem}_mlod.p3d")
    if sig == b"MLOD":
        with open(mlod_out_path, "wb") as f:
            f.write(p3d_bytes)
    elif sig == b"ODOL":
        tmp_odol = os.path.join(BRIDGE_DIR, f"{model_stem}.p3d")
        with open(tmp_odol, "wb") as f:
            f.write(p3d_bytes)
        if os.path.exists(DEBINARIZER_EXE):
            try:
                p = subprocess.Popen(
                    ["cmd.exe", "/c", DEBINARIZER_EXE, tmp_odol],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=BRIDGE_DIR,
                )
                p.wait(timeout=20)
            except Exception:
                pass
        if not os.path.exists(mlod_out_path) or os.path.getsize(mlod_out_path) == 0:
            raise RuntimeError("Не удалось дебинаризовать ODOL модель в MLOD")
    else:
        raise RuntimeError(f"Неизвестный формат P3D: {sig}")

    # 2. Parse MLOD geometry & material slots
    parsed_mlod = parse_full_mlod_for_blender(mlod_out_path)

    # 3. Extract & parse config.bin / config.cpp from PBO (or local directory)
    cfg_meta = {}
    pbo_name = os.path.splitext(os.path.basename(pbo_path))[0] if pbo_path else ""
    all_pbo_files = []
    if pbo_path and os.path.exists(pbo_path):
        _, entries, _ = read_pbo_index(pbo_path)
        all_pbo_files = [fn for fn, _ in entries]
        cfg_bytes = extract_from_pbo_by_path(pbo_path, "config.bin") or extract_from_pbo_by_path(pbo_path, "config.cpp")
        if cfg_bytes:
            cpp_text = decompile_config_bytes_to_text(cfg_bytes)
            by_model, _ = parse_config_cpp_classes(cpp_text, pbo_name=pbo_name)
            cfg_meta = by_model.get(model_base.lower(), {})

    # Fallback defaults if model had no direct config.cpp entry
    gear_type = cfg_meta.get("gear_type") or guess_gear_type_from_hints(
        model_path=model_file,
        pbo_name=pbo_name,
        class_name=model_stem,
    )
    item_name = cfg_meta.get("displayName") or model_stem
    class_slug = cfg_meta.get("class_name") or model_stem
    author_name = cfg_meta.get("author") or (pbo_name if pbo_name else "EZ_MOD Creator")
    mass_val = int(round(cfg_meta.get("mass") or parsed_mlod.get("mass") or 15))
    if mass_val <= 0:
        mass_val = 15
    armor_val = int(round(cfg_meta.get("armor") if "armor" in cfg_meta else (6 if gear_type == "HEADGEAR" else (20 if gear_type == "VEST" else 0))))
    load_val = int(round(cfg_meta.get("maximumLoad") if "maximumLoad" in cfg_meta else (160 if gear_type == "VEST" else (280 if gear_type == "BACKPACK" else 0))))
    hide_fp = bool(parsed_mlod.get("has_empty_view_pilot")) or (gear_type in ("HEADGEAR", "GOGGLES"))

    # Map config hiddenSelections -> hiddenSelectionsTextures
    hs_names = [s.lower() for s in cfg_meta.get("hiddenSelections", ["camo"])]
    hs_texs = cfg_meta.get("hiddenSelectionsTextures", [])
    config_slot_tex_map = {}
    for idx, sname in enumerate(hs_names):
        if idx < len(hs_texs) and hs_texs[idx]:
            config_slot_tex_map[sname] = hs_texs[idx]
    if skin_override and isinstance(skin_override, dict):
        for k, v in skin_override.items():
            if v:
                config_slot_tex_map[k.lower()] = v

    # 4. Resolve & convert Diffuse + Normal textures for every material slot
    paas_in_pbo = [f for f in all_pbo_files if f.lower().endswith(".paa") and "icon" not in f.lower()]
    m_dir = os.path.dirname(model_file).replace("\\", "/").lower()

    def _paa_to_png(paa_rel_path):
        if not paa_rel_path:
            return ""
        b = None
        if pbo_path and os.path.exists(pbo_path):
            b = extract_across_mod_pbos(pbo_path, paa_rel_path)
        elif os.path.exists(paa_rel_path):
            with open(paa_rel_path, "rb") as f:
                b = f.read()
        if not b:
            return ""
        stem = os.path.splitext(os.path.basename(paa_rel_path.replace("\\", "/")))[0]
        return _convert_paa_bytes_to_png_file(b, BRIDGE_TEX_DIR, f"{model_stem}_{stem}")

    resolved_slots = []
    for slot_info in parsed_mlod["material_slots"]:
        s_name = slot_info["slot_name"].lower()
        t_paa = slot_info["tex_paa"]
        rvmat = slot_info["rvmat"]

        diff_paa = ""
        norm_paa = ""

        # Priority 1: Explicit skin/config override for this camo slot
        if s_name in config_slot_tex_map:
            diff_paa = config_slot_tex_map[s_name]

        # Priority 2: Direct per-face texture assigned in P3D
        if not diff_paa and t_paa and t_paa.lower().endswith(".paa") and not t_paa.lower().startswith(("a3\\", "ca\\", "#")):
            diff_paa = t_paa

        # Priority 3: Derive from .rvmat path or inspect inside .rvmat
        if rvmat and rvmat.lower().endswith(".rvmat"):
            rv_no_ext = os.path.splitext(rvmat)[0]
            rv_stem = os.path.basename(rv_no_ext.replace("\\", "/")).lower()
            if not diff_paa and rv_stem in config_slot_tex_map:
                diff_paa = config_slot_tex_map[rv_stem]

            if not diff_paa:
                for suf in ("_co.paa", "_ca.paa"):
                    if pbo_path and extract_across_mod_pbos(pbo_path, rv_no_ext + suf):
                        diff_paa = rv_no_ext + suf
                        break

            if pbo_path and extract_across_mod_pbos(pbo_path, rv_no_ext + "_nohq.paa"):
                norm_paa = rv_no_ext + "_nohq.paa"

            if (not diff_paa or not norm_paa) and pbo_path:
                rv_bytes = extract_across_mod_pbos(pbo_path, rvmat)
                if rv_bytes:
                    for rp in re.findall(rb"[a-zA-Z0-9_\\/.:-]+\.paa", rv_bytes, re.I):
                        rps = rp.decode("latin1", errors="ignore").replace("/", "\\")
                        if rps.lower().startswith(("a3\\", "a2\\", "ca\\")):
                            continue
                        if "_nohq.paa" in rps.lower() and not norm_paa:
                            norm_paa = rps
                        elif ("_co.paa" in rps.lower() or "_ca.paa" in rps.lower()) and not diff_paa:
                            diff_paa = rps

        # Priority 4: Search in model folder inside PBO
        if not diff_paa and paas_in_pbo:
            for f in paas_in_pbo:
                fd = os.path.dirname(f).replace("\\", "/").lower()
                fb = os.path.basename(f).lower()
                if not m_dir or fd.startswith(m_dir):
                    if fb in (f"{s_name}_co.paa", f"{s_name}_ca.paa") or (fb.startswith(s_name + "_") and fb.endswith("_co.paa")):
                        diff_paa = f
                        break

        # Fallback: first config texture or first _co.paa in model dir
        if not diff_paa and hs_texs:
            diff_paa = hs_texs[0]
        if not diff_paa and paas_in_pbo:
            for f in paas_in_pbo:
                fd = os.path.dirname(f).replace("\\", "/").lower()
                fb = os.path.basename(f).lower()
                if (not m_dir or fd.startswith(m_dir)) and (fb.endswith("_co.paa") or fb.endswith("_ca.paa")):
                    diff_paa = f
                    break

        if diff_paa and not norm_paa:
            cand_nohq = re.sub(r"_(co|ca)\.paa$", "_nohq.paa", diff_paa, flags=re.I)
            if cand_nohq != diff_paa and pbo_path and extract_across_mod_pbos(pbo_path, cand_nohq):
                norm_paa = cand_nohq

        if not norm_paa and paas_in_pbo:
            for f in paas_in_pbo:
                fd = os.path.dirname(f).replace("\\", "/").lower()
                fb = os.path.basename(f).lower()
                if (not m_dir or fd.startswith(m_dir)) and fb.endswith("_nohq.paa"):
                    norm_paa = f
                    break

        diff_png = _paa_to_png(diff_paa)
        norm_png = _paa_to_png(norm_paa)

        resolved_slots.append(
            {
                "slot_name": slot_info["slot_name"],
                "diffuse_png": diff_png,
                "normal_png": norm_png,
                "diffuse_paa": diff_paa,
                "normal_paa": norm_paa,
                "rvmat": rvmat,
            }
        )

    # Also convert variant skin textures if multiple variants exist in config
    variants_out = []
    for var in cfg_meta.get("variants", [])[:12]:
        v_texs = var.get("textures", [])
        v_pngs = [_paa_to_png(tp) for tp in v_texs]
        variants_out.append(
            {
                "class_name": var.get("class_name", ""),
                "displayName": var.get("displayName", ""),
                "diffuse_pngs": v_pngs,
            }
        )

    manifest = {
        "model_stem": model_stem,
        "model_filename": model_base,
        "pbo_path": pbo_path,
        "mlod_p3d_path": mlod_out_path,
        "item_name": item_name,
        "class_slug": class_slug,
        "author_name": author_name,
        "description": cfg_meta.get("description", ""),
        "gear_type": gear_type,
        "armor_val": armor_val,
        "mass_val": mass_val,
        "load_val": load_val,
        "hide_in_first_person": hide_fp,
        "vertex_count": len(parsed_mlod["verts_bl"]),
        "face_count": len(parsed_mlod["faces"]),
        "material_slots": resolved_slots,
        "variants": variants_out,
    }

    with open(BRIDGE_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Save into history queue as well
    history = []
    if os.path.exists(BRIDGE_HISTORY):
        try:
            with open(BRIDGE_HISTORY, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    history = [h for h in history if h.get("mlod_p3d_path") != mlod_out_path]
    history.insert(0, manifest)
    history = history[:25]
    with open(BRIDGE_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    return manifest
