import os
import sys
import re
import json
import struct
import subprocess
import hashlib
import urllib.request
import urllib.parse
import threading
import webbrowser
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
import ez_mod_core

WORKSHOP_DIR = r"Z:\SteamLibrary\steamapps\common\Arma 3\!Workshop"
APP_DIR = r"Z:\aarma"
THUMBS_DIR = os.path.join(APP_DIR, ".cache_thumbs")
STATE_FILE = os.path.join(APP_DIR, "mod_states.json")
EXTRACT_BASE = os.path.join(APP_DIR, "extracted")
TOOLS_DIR = os.path.join(APP_DIR, "tools")
DEBINARIZER_EXE = os.path.join(TOOLS_DIR, "P3DDebinarizer.exe")
PAL2PAC_DIR = r"Z:\SteamLibrary\steamapps\common\Arma 3 Tools\TexView2"
PAL2PAC_EXE = os.path.join(PAL2PAC_DIR, "Pal2PacE.exe")

os.makedirs(THUMBS_DIR, exist_ok=True)
os.makedirs(EXTRACT_BASE, exist_ok=True)
os.makedirs(TOOLS_DIR, exist_ok=True)

CATEGORIES = {
    "gear": ["экипировка", ["gear", "vest", "helmet", "uniform", "plate", "carrier", "ghillie", "camo", "pouch", "armor", "sso", "seal", "mask", "bag", "backpack", "nvg", "headset", "goggles", "hat", "armband", "ares"]],
    "weapons": ["оружие", ["weapon", "carbine", "sniper", "gun", "rifle", "apr", "optics", "suppressor", "magazine", "jca", "tier one"]],
    "anims": ["анимации и звуки", ["anim", "sound", "jsrs", "blood", "ragdoll", "smoke", "grenade", "movement", "vault", "reaction", "effects"]],
    "maps": ["карты", ["terrain", "map", "chernarus", "esseker", "jbad", "korsac", "lythium", "weather", "lighting", "grass", "vinjesvingen", "japan"]],
    "vehicles": ["техника", ["vehicle", "apalon", "tank", "plane", "heli", "car", "ship", "drone", "turret", "convoy"]],
    "core": ["core / система", ["ace", "cba", "enhanced", "zeus", "tfar", "radio", "lambs", "core", "achilles", "gps", "hud", "radar", "simplex", "aim assist"]]
}

def guess_category(name, folder):
    text = (name + " " + folder).lower()
    for cat_id, (cat_label, keywords) in CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return cat_id, cat_label
    return "other", "прочее"

def load_states():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_states(states):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(states, f, indent=2, ensure_ascii=False)

def get_cached_thumb(pub_id):
    if not pub_id:
        return None
    p = os.path.join(THUMBS_DIR, f"{pub_id}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return f"/api/thumb?id={pub_id}"
    return None

def fetch_workshop_thumbnails(mods_data):
    ids_to_fetch = [m["pub_id"] for m in mods_data if m["pub_id"] and not os.path.exists(os.path.join(THUMBS_DIR, f"{m['pub_id']}.jpg"))]
    if not ids_to_fetch:
        return
        
    chunk_size = 50
    for i in range(0, len(ids_to_fetch), chunk_size):
        chunk = ids_to_fetch[i:i+chunk_size]
        post_data = {"itemcount": len(chunk)}
        for idx, fid in enumerate(chunk):
            post_data[f"publishedfileids[{idx}]"] = fid
            
        try:
            req = urllib.request.Request(
                "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                data=urllib.parse.urlencode(post_data).encode("utf-8")
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                for item in res.get("response", {}).get("publishedfiledetails", []):
                    fid = str(item.get("publishedfileid"))
                    preview_url = item.get("preview_url")
                    if preview_url:
                        try:
                            thumb_path = os.path.join(THUMBS_DIR, f"{fid}.jpg")
                            urllib.request.urlretrieve(preview_url, thumb_path)
                        except Exception:
                            pass
        except Exception as e:
            print("Workshop API error:", e)

def scan_workshop_mods():
    states = load_states()
    mods = []
    
    if not os.path.exists(WORKSHOP_DIR):
        return []
        
    for item in os.listdir(WORKSHOP_DIR):
        p = os.path.join(WORKSHOP_DIR, item)
        if not os.path.isdir(p) or not item.startswith("@"):
            continue
            
        meta_file = os.path.join(p, "meta.cpp")
        mod_file = os.path.join(p, "mod.cpp")
        pub_id = None
        name = item.lstrip("@")
        author = ""
        
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8", errors="ignore") as f:
                    c = f.read()
                    m_id = re.search(r"publishedid\s*=\s*(\d+);", c)
                    if m_id and m_id.group(1) != "0":
                        pub_id = m_id.group(1)
                    m_name = re.search(r'name\s*=\s*"([^"]+)";', c)
                    if m_name:
                        name = m_name.group(1)
            except Exception:
                pass
                
        if os.path.exists(mod_file):
            try:
                with open(mod_file, "r", encoding="utf-8", errors="ignore") as f:
                    c = f.read()
                    if not pub_id:
                        m_id = re.search(r"id\s*=\s*(\d+);", c) or re.search(r"\?id=(\d+)", c)
                        if m_id:
                            pub_id = m_id.group(1)
                    m_auth = re.search(r'author\s*=\s*"([^"]+)";', c)
                    if m_auth:
                        author = m_auth.group(1)
            except Exception:
                pass

        addons_dir = os.path.join(p, "addons")
        pbo_count = 0
        total_size = 0
        pbo_list = []
        if os.path.exists(addons_dir):
            for fn in os.listdir(addons_dir):
                if fn.lower().endswith(".pbo"):
                    pbo_count += 1
                    fp = os.path.join(addons_dir, fn)
                    sz = os.path.getsize(fp)
                    total_size += sz
                    pbo_list.append({"name": fn, "size": sz})
        else:
            for root, _, fns in os.walk(p):
                for fn in fns:
                    total_size += os.path.getsize(os.path.join(root, fn))

        cat_id, cat_label = guess_category(name, item)
        
        local_img = None
        for img_fn in ["logo_steam.jpg", "logo.jpg", "overview.jpg", "picture.jpg", "logo.png", "picture.png"]:
            cand = os.path.join(p, img_fn)
            if os.path.exists(cand):
                local_img = f"/api/local-image?path={urllib.parse.quote(cand)}"
                break
                
        thumb_url = local_img or get_cached_thumb(pub_id)
        enabled = states.get(item, True)

        mods.append({
            "folder": item,
            "full_path": p,
            "name": name,
            "author": author if author else "Неизвестен",
            "pub_id": pub_id or "0",
            "category": cat_id,
            "category_label": cat_label.upper(),
            "size": total_size,
            "size_formatted": f"{total_size / 1073741824:.2f} GB" if total_size > 1073741824 else f"{total_size / 1048576:.1f} MB",
            "pbo_count": pbo_count,
            "pbo_list": pbo_list,
            "thumb_url": thumb_url,
            "enabled": enabled
        })
        
    return sorted(mods, key=lambda x: x["name"].lower())

def read_cstring(f):
    chars = []
    while True:
        c = f.read(1)
        if not c or c == b'\x00': break
        chars.append(c)
    return b''.join(chars).decode('latin1', errors='replace')

def parse_config_model_mappings(cfg_bytes, pbo_name=""):
    mapping = {}
    if not cfg_bytes:
        return mapping

    # Primary: use CfgConvert.exe -txt + structured inheritance parser
    try:
        cpp_text = ez_mod_core.decompile_config_bytes_to_text(cfg_bytes)
        if cpp_text:
            by_model, _ = ez_mod_core.parse_config_cpp_classes(cpp_text, pbo_name=pbo_name)
            if by_model:
                mapping.update(by_model)
    except Exception:
        pass
        
    for m in re.finditer(rb'model[a-zA-Z0-9_]*\x00([^\x00]+\.p3d)\x00', cfg_bytes, re.I):
        try:
            model_path = m.group(1).decode('latin1', errors='ignore')
            model_fn = os.path.basename(model_path).lower()
            if model_fn in mapping:
                continue
            
            chunk_start = max(0, m.start() - 500)
            chunk_end = min(len(cfg_bytes), m.end() + 500)
            chunk = cfg_bytes[chunk_start:chunk_end]
            
            dn_match = re.search(rb'displayName\x00([^\x00]+)\x00', chunk, re.I)
            dn = dn_match.group(1).decode('latin1', errors='ignore') if dn_match else None
            
            desc_match = re.search(rb'descriptionShort\x00([^\x00]+)\x00', chunk, re.I)
            desc = desc_match.group(1).decode('latin1', errors='ignore') if desc_match else None
            
            pic_match = re.search(rb'picture\x00([^\x00]+)\x00', chunk, re.I)
            pic = pic_match.group(1).decode('latin1', errors='ignore') if pic_match else None
            
            auth_match = re.search(rb'author\x00([^\x00]+)\x00', chunk, re.I)
            auth = auth_match.group(1).decode('latin1', errors='ignore') if auth_match else None

            tex_match = re.search(rb'hiddenSelectionsTextures\x00[^\x00]*\x00([^\x00]+\.paa)\x00', chunk, re.I)
            tex = tex_match.group(1).decode('latin1', errors='ignore') if tex_match else None

            mapping[model_fn] = {
                'class_name': os.path.splitext(model_fn)[0],
                'displayName': dn or os.path.splitext(model_fn)[0],
                'description': desc or '',
                'picture': pic or '',
                'author': auth or '',
                'texture': tex or '',
                'full_model_path': model_path,
                'gear_type': ez_mod_core.guess_gear_type_from_hints(model_path=model_path, pbo_name=pbo_name),
                'mass': 15,
                'armor': 6,
                'maximumLoad': 0,
            }
        except Exception:
            pass
            
    return mapping

def get_pbo_details(pbo_path):
    pbo_name = os.path.splitext(os.path.basename(pbo_path))[0] if pbo_path else ""
    with open(pbo_path, 'rb') as f:
        read_cstring(f)
        f.read(20)
        props = {}
        while True:
            k = read_cstring(f)
            if not k: break
            props[k] = read_cstring(f)
            
        entries = []
        cfg_bytes = None
        while True:
            fn = read_cstring(f)
            magic, orig_sz, res, ts, data_sz = struct.unpack('<IIIII', f.read(20))
            if not fn and data_sz == 0: break
            entries.append({'filename': fn, 'size': data_sz, 'p3d_type': '-', 'display_name': None, 'description': None, 'texture': None})
            
        for item in entries:
            sz = item['size']
            fn_lower = item['filename'].lower()
            if fn_lower in ['config.bin', 'config.cpp']:
                cfg_bytes = f.read(sz)
            elif fn_lower.endswith('.p3d') and sz >= 4:
                pos = f.tell()
                head4 = f.read(4)
                if head4 == b'MLOD':
                    item['p3d_type'] = 'MLOD (Blender OK)'
                elif head4 == b'ODOL':
                    item['p3d_type'] = 'ODOL (Бинарник)'
                else:
                    item['p3d_type'] = head4.decode('ascii', errors='replace')
                f.seek(pos + sz)
            else:
                f.seek(sz, os.SEEK_CUR)
                
    if cfg_bytes:
        mapping = parse_config_model_mappings(cfg_bytes, pbo_name=pbo_name)
        for item in entries:
            base_fn = os.path.basename(item['filename']).lower()
            if base_fn in mapping:
                info = mapping[base_fn]
                item['display_name'] = info['displayName']
                item['description'] = info['description']
                item['picture'] = info['picture']
                item['author'] = info['author']
                item['texture'] = info['texture']
                item['class_name'] = info.get('class_name', '')
                item['gear_type'] = info.get('gear_type', '')
                item['mass'] = info.get('mass', 0)
                item['armor'] = info.get('armor', 0)
                item['maximumLoad'] = info.get('maximumLoad', 0)

    return props.get('prefix', ''), entries

def unpack_single_pbo(pbo_path, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    with open(pbo_path, 'rb') as f:
        read_cstring(f)
        f.read(20)
        while True:
            k = read_cstring(f)
            if not k: break
            read_cstring(f)
            
        entries = []
        while True:
            fn = read_cstring(f)
            magic, orig_sz, res, ts, data_sz = struct.unpack('<IIIII', f.read(20))
            if not fn and data_sz == 0: break
            entries.append((fn, data_sz))
            
        for fn, sz in entries:
            data = f.read(sz)
            out_path = os.path.join(dest_dir, fn.replace('/', os.sep).replace('\\', os.sep))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as out_f:
                out_f.write(data)

def extract_file_from_pbo(pbo_path, target_fn, strict_prefix=False):
    if not pbo_path or not os.path.exists(pbo_path) or not target_fn:
        return None
    clean_target = target_fn.replace('\\', '/').lower().lstrip('/')
    with open(pbo_path, 'rb') as f:
        read_cstring(f)
        f.read(20)
        pbo_prefix = ""
        while True:
            k = read_cstring(f)
            if not k: break
            v = read_cstring(f)
            if k.lower() == 'prefix':
                pbo_prefix = v.replace('\\', '/').lower().strip('/')
            
        entries = []
        while True:
            fn = read_cstring(f)
            magic, orig_sz, res, ts, data_sz = struct.unpack('<IIIII', f.read(20))
            if not fn and data_sz == 0: break
            entries.append((fn, data_sz))
            
        rel_target = clean_target
        if pbo_prefix and clean_target.startswith(pbo_prefix + '/'):
            rel_target = clean_target[len(pbo_prefix) + 1:]

        for fn, sz in entries:
            clean_fn = fn.replace('\\', '/').lower().lstrip('/')
            if clean_fn == clean_target or clean_fn == rel_target:
                return f.read(sz)
            elif not strict_prefix and (clean_fn.endswith('/' + clean_target) or clean_target.endswith('/' + clean_fn)):
                return f.read(sz)
            else:
                f.seek(sz, os.SEEK_CUR)
    return None

def extract_file_across_mod(pbo_path, target_fn):
    """Extracts a file from pbo_path, or from a sibling PBO in the same @Mod/addons directory if prefixed (e.g. age_weapons\\...)."""
    if not pbo_path or not target_fn:
        return None
    clean_t = target_fn.replace('\\', '/').lstrip('/')
    parts = clean_t.split('/')
    if len(parts) > 1:
        addons_dir = os.path.dirname(pbo_path)
        pbo_stem = os.path.splitext(os.path.basename(pbo_path))[0].lower()
        first_seg = parts[0].lower()
        # If first segment explicitly names a sibling PBO (like age_weapons when we are in AGE_Helmets)
        if first_seg != pbo_stem and os.path.exists(addons_dir):
            for f in os.listdir(addons_dir):
                if f.lower() == first_seg + '.pbo':
                    sib_pbo = os.path.join(addons_dir, f)
                    b = extract_file_from_pbo(sib_pbo, clean_t, strict_prefix=True)
                    if b: return b
                    b = extract_file_from_pbo(sib_pbo, '/'.join(parts[1:]), strict_prefix=True)
                    if b: return b
    b = extract_file_from_pbo(pbo_path, clean_t, strict_prefix=True)
    if b:
        return b
    b = extract_file_from_pbo(pbo_path, clean_t, strict_prefix=False)
    if b:
        return b
    if len(parts) > 1 and os.path.exists(os.path.dirname(pbo_path)):
        addons_dir = os.path.dirname(pbo_path)
        for f in os.listdir(addons_dir):
            if f.lower().endswith('.pbo') and os.path.join(addons_dir, f) != pbo_path:
                sib_pbo = os.path.join(addons_dir, f)
                b = extract_file_from_pbo(sib_pbo, clean_t, strict_prefix=True)
                if b: return b
    return None

def debinarize_odol_bytes(odol_bytes, safe_name):
    if not os.path.exists(DEBINARIZER_EXE):
        return None, None
        
    cache_in = os.path.join(THUMBS_DIR, f"{safe_name}.p3d")
    cache_out = os.path.join(THUMBS_DIR, f"{safe_name}_mlod.p3d")
    
    if os.path.exists(cache_out) and os.path.getsize(cache_out) > 0:
        with open(cache_out, "rb") as f:
            return f.read(), cache_out

    with open(cache_in, "wb") as f:
        f.write(odol_bytes)
        
    try:
        p = subprocess.Popen(["cmd.exe", "/c", DEBINARIZER_EXE, cache_in], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=os.path.dirname(cache_in))
        p.wait(timeout=15)
    except Exception as e:
        print("Debinarize error:", e)
        return None, None
        
    if os.path.exists(cache_out) and os.path.getsize(cache_out) > 0:
        with open(cache_out, "rb") as f:
            return f.read(), cache_out
            
    return None, None

def convert_paa_to_png(paa_bytes, cache_name):
    """Converts Bohemia PAA bytes to PNG via Pal2PacE.exe using content hash to prevent collisions"""
    if not os.path.exists(PAL2PAC_EXE) or not paa_bytes:
        return None
        
    h = hashlib.md5(paa_bytes).hexdigest()[:12]
    safe_label = re.sub(r'[^a-zA-Z0-9_]', '_', cache_name)
    safe_cache_name = f"{safe_label}_{h}"
    
    png_file = os.path.join(THUMBS_DIR, f"{safe_cache_name}.png")
    if os.path.exists(png_file) and os.path.getsize(png_file) > 0:
        return f"/api/texture?name={safe_cache_name}"
        
    paa_file = os.path.join(THUMBS_DIR, f"{safe_cache_name}.paa")
    with open(paa_file, "wb") as f:
        f.write(paa_bytes)
        
    try:
        res = subprocess.run([PAL2PAC_EXE, paa_file, png_file], cwd=PAL2PAC_DIR, capture_output=True, timeout=10)
    except Exception as e:
        print("Pal2Pac error:", e)
        return None
        
    if os.path.exists(png_file) and os.path.getsize(png_file) > 0:
        return f"/api/texture?name={safe_cache_name}"
    return None


def resolve_model_textures(pbo_path, model_file, hint_tex=None, raw_p3d_bytes=b""):
    """Smartly resolves diffuse textures and normal maps for a model inside a PBO."""
    diff_cands = []
    norm_cand = None
    if hint_tex:
        diff_cands.append(hint_tex)
        
    try:
        _, entries = get_pbo_details(pbo_path)
        all_files = [e['filename'] for e in entries]
    except Exception:
        all_files = []
        
    paas = [f for f in all_files if f.lower().endswith('.paa') and 'icon' not in f.lower()]
    
    if raw_p3d_bytes:
        raw_paas = re.findall(rb'[a-zA-Z0-9_\\/.:-]+\.paa', raw_p3d_bytes, re.I)
        for rp in raw_paas:
            rps = rp.decode('latin1', errors='ignore').replace('/', '\\')
            if rps.lower().startswith(('a3\\', 'a2\\', 'ca\\')):
                continue
            if '_nohq.paa' in rps.lower() and not norm_cand:
                norm_cand = rps
            elif '_co.paa' in rps.lower() or '_ca.paa' in rps.lower():
                if rps not in diff_cands:
                    diff_cands.append(rps)
                    
    m_dir = os.path.dirname(model_file).replace('\\', '/').lower()
    m_base = os.path.splitext(os.path.basename(model_file))[0].lower()
    
    for f in paas:
        f_dir = os.path.dirname(f).replace('\\', '/').lower()
        if f_dir == m_dir or f_dir == m_dir + '/textures' or (m_dir and f_dir.startswith(m_dir)):
            fl = f.lower()
            if '_nohq.paa' in fl and not norm_cand:
                norm_cand = f
            elif '_co.paa' in fl or '_ca.paa' in fl or '_camo' in fl:
                if f not in diff_cands:
                    diff_cands.append(f)
                    
    if norm_cand and not diff_cands:
        try_co = re.sub(r'_nohq\.paa', '_co.paa', norm_cand, flags=re.I)
        diff_cands.append(try_co)
        
    def score(f):
        fn = os.path.basename(f).lower()
        s = 0
        for token in m_base.split('_'):
            if token and token in fn: s += 10
        if '_co.paa' in fn: s += 5
        return -s
    diff_cands.sort(key=score)
    
    textures_list = []
    main_tex_url = None
    for c in diff_cands:
        b = extract_file_from_pbo(pbo_path, c)
        if b:
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.basename(c).replace('.paa', ''))
            url = convert_paa_to_png(b, safe_name)
            if url:
                if not main_tex_url:
                    main_tex_url = url
                tex_title = os.path.basename(c).replace('.paa', '')
                if not any(t['name'] == tex_title for t in textures_list):
                    textures_list.append({"name": tex_title, "url": url})
                if len(textures_list) >= 6:
                    break
                    
    normal_url = None
    if norm_cand:
        nb = extract_file_from_pbo(pbo_path, norm_cand)
        if nb:
            safe_norm_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.basename(norm_cand).replace('.paa', ''))
            normal_url = convert_paa_to_png(nb, safe_norm_name)
            
    return main_tex_url, normal_url, textures_list

def parse_mlod_bytes(data):

    version, num_lods = struct.unpack('<II', data[4:12])
    pos = 12
    lods = []
    
    for lod_idx in range(num_lods):
        if pos + 28 > len(data): break
        lsig = data[pos:pos+4]
        if lsig != b'P3DM': break
        major, minor, num_pts, num_nrm, num_faces, dummy = struct.unpack('<IIIIII', data[pos+4:pos+28])
        pos += 28
        
        pts = []
        for _ in range(num_pts):
            x, y, z, flags = struct.unpack('<fffi', data[pos:pos+16])
            # Convert Arma left-handed (X left, Y up, Z back) -> Three.js right-handed (X right, Y up, Z front)
            pts.append([round(x, 4), round(y, 4), round(-z, 4)])
            pos += 16
            
        nrms = []
        for _ in range(num_nrm):
            nx, ny, nz = struct.unpack('<fff', data[pos:pos+12])
            nrms.append([round(nx, 4), round(ny, 4), round(-nz, 4)])
            pos += 12
            
        textures_found = []
        face_records = []
        
        for _ in range(num_faces):
            sides = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
            v_idx = []
            n_idx = []
            uvs = []
            for n in range(4):
                vi, ni, u, v = struct.unpack('<iiff', data[pos:pos+16])
                pos += 16
                if n < sides:
                    v_idx.append(vi)
                    n_idx.append(ni)
                    uvs.append([round(u, 4), round(1.0 - v, 4)])
            flags = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
            t_end = data.find(b'\x00', pos)
            tex_str = data[pos:t_end].decode('latin1', errors='ignore').strip()
            pos = t_end + 1
            m_end = data.find(b'\x00', pos)
            mat_str = data[pos:m_end].decode('latin1', errors='ignore').strip()
            pos = m_end + 1
            if tex_str and tex_str not in textures_found:
                textures_found.append(tex_str)
                
            tris = []
            if sides == 3:
                tris = [(0, 2, 1)]
            elif sides == 4:
                tris = [(0, 2, 1), (0, 3, 2)]
                
            f_pos = []
            f_nrm = []
            f_uvs = []
            for t in tris:
                for idx in t:
                    v = v_idx[idx]
                    f_pos.extend(pts[v])
                    if n_idx[idx] < len(nrms):
                        f_nrm.extend(nrms[n_idx[idx]])
                    else:
                        f_nrm.extend([0, 1, 0])
                    f_uvs.extend(uvs[idx])
            face_records.append((f_pos, f_nrm, f_uvs, tex_str, mat_str))
                
        selections = []
        face_camo_slot = {}
        proxy_face_set = set()
        mass = 0.0
        if pos + 4 <= len(data) and data[pos:pos+4] == b'TAGG':
            pos += 4
            while pos + 6 <= len(data):
                active = data[pos:pos+1]
                pos += 1
                t_end = data.find(b'\x00', pos)
                if t_end == -1: break
                tag_name = data[pos:t_end].decode('latin1', errors='replace')
                pos = t_end + 1
                tag_sz = struct.unpack('<I', data[pos:pos+4])[0]
                pos += 4
                if tag_name == "#EndOfFile#":
                    break
                elif tag_name == "#Mass#":
                    for _ in range(num_pts):
                        if pos + 4 <= len(data):
                            mass += struct.unpack('<f', data[pos:pos+4])[0]
                            pos += 4
                elif not tag_name.startswith("#") and len(tag_name) > 0:
                    selections.append(tag_name)
                    tl = tag_name.lower()
                    if tl.startswith("proxy:") and tag_sz >= num_pts + num_faces:
                        f_bytes = data[pos + num_pts : pos + num_pts + num_faces]
                        for fi, bval in enumerate(f_bytes):
                            if bval > 0:
                                proxy_face_set.add(fi)
                    elif tl.startswith("camo") and tag_sz >= num_pts + num_faces:
                        f_bytes = data[pos + num_pts : pos + num_pts + num_faces]
                        for fi, bval in enumerate(f_bytes):
                            if bval > 0 and fi not in face_camo_slot:
                                face_camo_slot[fi] = tl
                    pos += tag_sz
                else:
                    pos += tag_sz
                    
        # Group faces by (tex_str, mat_str, camo_slot) so multi-material models render each sub-asset with its own texture
        grouped = {}
        group_order = []
        for fi, (f_pos, f_nrm, f_uvs, tex_str, mat_str) in enumerate(face_records):
            if not f_pos or fi in proxy_face_set:
                continue
            c_slot = face_camo_slot.get(fi, "")
            key = (tex_str, mat_str, c_slot)
            if key not in grouped:
                grouped[key] = [[], [], []]
                group_order.append(key)
            grouped[key][0].extend(f_pos)
            grouped[key][1].extend(f_nrm)
            grouped[key][2].extend(f_uvs)

        flat_pos = []
        flat_nrm = []
        flat_uvs = []
        submeshes = []
        for key in group_order:
            g_pos, g_nrm, g_uvs = grouped[key]
            start_v = len(flat_pos) // 3
            count_v = len(g_pos) // 3
            flat_pos.extend(g_pos)
            flat_nrm.extend(g_nrm)
            flat_uvs.extend(g_uvs)
            submeshes.append({
                "start": start_v,
                "count": count_v,
                "tex": key[0],
                "mat": key[1],
                "slot": key[2]
            })

        res = 0.0
        if pos + 4 <= len(data):
            res = struct.unpack('<f', data[pos:pos+4])[0]
            pos += 4
            
        lod_label = f"LOD {lod_idx}: Resolution {res:.3f}"
        if res >= 1e15: lod_label = f"LOD {lod_idx}: Geometry / Collision"
        elif res >= 1e4: lod_label = f"LOD {lod_idx}: Shadow Volume"
        elif res == 1200: lod_label = f"LOD {lod_idx}: View Cargo"
        
        lods.append({
            "index": lod_idx,
            "label": lod_label,
            "resolution": res,
            "vertex_count": num_pts,
            "face_count": num_faces,
            "flat_positions": flat_pos,
            "flat_normals": flat_nrm,
            "flat_uvs": flat_uvs,
            "submeshes": submeshes,
            "textures": textures_found,
            "selections": selections,
            "mass": round(mass, 2)
        })
        
    return {
        "type": "MLOD",
        "version": version,
        "lods": lods,
        "total_lods": len(lods)
    }


def resolve_submeshes_and_skins(pbo_path, model_file, hint_tex, raw_p3d_bytes, parsed):
    """Resolves per-submesh diffuse & normal textures and builds multi-material skin presets."""
    main_tex_url, fallback_norm_url, legacy_tex_list = resolve_model_textures(pbo_path, model_file, hint_tex, raw_p3d_bytes)
    parsed["texture_url"] = main_tex_url
    parsed["normal_url"] = fallback_norm_url
    parsed["textures"] = legacy_tex_list

    if not pbo_path or not os.path.exists(pbo_path):
        return

    try:
        _, entries = get_pbo_details(pbo_path)
        all_files = [e['filename'] for e in entries]
    except Exception:
        all_files = []

    paas = [f for f in all_files if f.lower().endswith('.paa') and 'icon' not in f.lower()]
    m_dir = os.path.dirname(model_file).replace('\\', '/').lower()

    def paa_path_to_url(target_paa):
        if not target_paa:
            return None
        b = extract_file_across_mod(pbo_path, target_paa)
        if not b:
            return None
        safe_n = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(os.path.basename(target_paa))[0])
        return convert_paa_to_png(b, safe_n)

    resolved_cache = {}
    active_stems = set()

    for lod in parsed.get("lods", []):
        for sub in lod.get("submeshes", []):
            t_str = sub.get("tex", "")
            m_str = sub.get("mat", "")
            c_slot = sub.get("slot", "")
            cache_key = (t_str.lower(), m_str.lower(), c_slot.lower())

            if cache_key not in resolved_cache:
                diff_url = None
                norm_url = None
                slot_id = c_slot.lower() if c_slot else "camo"

                # 1. Explicit per-face texture (e.g. age_weapons\data\acc\camo_co.paa or magazine_co.paa)
                if t_str and t_str.lower().endswith('.paa') and not t_str.lower().startswith(('a3\\', 'ca\\', '#')):
                    diff_url = paa_path_to_url(t_str)
                    try_nohq = re.sub(r'_(co|ca)\.paa$', '_nohq.paa', t_str, flags=re.I)
                    if try_nohq != t_str:
                        norm_url = paa_path_to_url(try_nohq)
                    slot_id = "fixed:" + os.path.basename(t_str).lower()

                # 2. Per-face RVMAT material (e.g. camo.rvmat, camo1.rvmat, camo2.rvmat, body.rvmat, mods.rvmat)
                if not diff_url and m_str and m_str.lower().endswith('.rvmat'):
                    mat_no_ext = os.path.splitext(m_str)[0]
                    mat_stem = os.path.basename(mat_no_ext).lower()
                    slot_id = mat_stem
                    active_stems.add(mat_stem)

                    # Try direct <mat_no_ext>_co.paa / _ca.paa
                    for suf in ('_co.paa', '_ca.paa'):
                        diff_url = paa_path_to_url(mat_no_ext + suf)
                        if diff_url:
                            break

                    # Try direct <mat_no_ext>_nohq.paa
                    norm_url = paa_path_to_url(mat_no_ext + '_nohq.paa')

                    # Inspect inside .rvmat if needed
                    if not diff_url or not norm_url:
                        rv_bytes = extract_file_across_mod(pbo_path, m_str)
                        if rv_bytes:
                            rv_paas = re.findall(rb'[a-zA-Z0-9_\\/.:-]+\.paa', rv_bytes, re.I)
                            for rp in rv_paas:
                                rps = rp.decode('latin1', errors='ignore').replace('/', '\\')
                                if rps.lower().startswith(('a3\\', 'a2\\', 'ca\\')):
                                    continue
                                if '_nohq.paa' in rps.lower() and not norm_url:
                                    norm_url = paa_path_to_url(rps)
                                elif ('_co.paa' in rps.lower() or '_ca.paa' in rps.lower()) and not diff_url:
                                    diff_url = paa_path_to_url(rps)

                    # Search in model directory inside PBO for matching stem (e.g. camo1_co.paa or subfolder/camo_co.paa)
                    if not diff_url:
                        for f in paas:
                            f_dir = os.path.dirname(f).replace('\\', '/').lower()
                            f_base = os.path.basename(f).lower()
                            if not m_dir or f_dir.startswith(m_dir):
                                if f_base == f"{mat_stem}_co.paa" or f_base == f"{mat_stem}_ca.paa" or f_base.startswith(f"{mat_stem}_") and f_base.endswith("_co.paa"):
                                    diff_url = paa_path_to_url(f)
                                    if diff_url:
                                        break

                # 3. Fallback to model-level main texture
                if not diff_url:
                    diff_url = main_tex_url
                if not norm_url and not slot_id.startswith("fixed:"):
                    norm_url = fallback_norm_url

                resolved_cache[cache_key] = (diff_url, norm_url, slot_id)

            d_url, n_url, s_id = resolved_cache[cache_key]
            sub["diffuse_url"] = d_url
            sub["normal_url"] = n_url
            sub["slot_id"] = s_id
            if d_url and not parsed["texture_url"]:
                parsed["texture_url"] = d_url
            if n_url and not parsed["normal_url"]:
                parsed["normal_url"] = n_url

    # Build multi-material skin presets for the model
    if not active_stems:
        active_stems = {"camo"}

    # Scan model directory paas for skin variants of active_stems
    # e.g. camo_co.paa, camo_black_co.paa, camo1_black_co.paa, NoFlag/camo1_black_co.paa, etc.
    variant_map = {}  # variant_label -> { stem: internal_paa_path }
    base_stems_files = {}

    for f in paas:
        f_dir = os.path.dirname(f).replace('\\', '/').lower()
        if m_dir and not f_dir.startswith(m_dir):
            continue
        f_base = os.path.basename(f)
        f_low = f_base.lower()
        if not (f_low.endswith('_co.paa') or f_low.endswith('_ca.paa')):
            continue
        if '_nohq' in f_low or '_smdi' in f_low or '_as.' in f_low:
            continue

        # Check subfolder relative to textures dir
        rel_dir = f_dir[len(m_dir):].strip('/')
        if rel_dir in ('data', 'textures'):
            sub_tag = ''
        elif rel_dir.startswith('textures/'):
            sub_tag = rel_dir[len('textures/'):].strip('/')
        elif rel_dir.startswith('data/'):
            sub_tag = rel_dir[len('data/'):].strip('/')
        else:
            sub_tag = rel_dir

        matched_stem = None
        for st in sorted(active_stems, key=len, reverse=True):
            if f_low == f"{st}_co.paa" or f_low == f"{st}_ca.paa":
                matched_stem = st
                var_suffix = ""
                break
            elif f_low.startswith(st + "_") and (f_low.endswith("_co.paa") or f_low.endswith("_ca.paa")):
                matched_stem = st
                var_suffix = f_low[len(st) + 1 : -7]
                break

        if matched_stem:
            if not var_suffix and not sub_tag:
                v_name = "Стандарт (Base)"
                base_stems_files[matched_stem] = f
            elif sub_tag and var_suffix:
                v_name = f"{sub_tag} / {var_suffix}"
            elif sub_tag:
                v_name = sub_tag
            else:
                v_name = var_suffix
            variant_map.setdefault(v_name, {})[matched_stem] = f

    skin_presets = []
    # Put "Стандарт (Base)" first if present
    ordered_vars = sorted(variant_map.keys(), key=lambda x: (0 if x == "Стандарт (Base)" else 1, x))
    for v_name in ordered_vars[:12]:
        st_dict = variant_map[v_name]
        slot_urls = {}
        for st in active_stems:
            chosen_paa = st_dict.get(st) or base_stems_files.get(st)
            if chosen_paa:
                slot_urls[st] = f"/api/pbo-texture?pbo={urllib.parse.quote(pbo_path)}&file={urllib.parse.quote(chosen_paa)}"
        if slot_urls:
            skin_presets.append({
                "name": v_name,
                "slot_urls": slot_urls
            })

    parsed["skin_presets"] = skin_presets

class ModManagerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)

        if path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_UI.encode("utf-8"))
            return

        elif path == "/api/three.js":
            p = os.path.join(THUMBS_DIR, "three.min.js")
            if os.path.exists(p):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                with open(p, "rb") as f: self.wfile.write(f.read())
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/orbit-controls.js":
            p = os.path.join(THUMBS_DIR, "OrbitControls.js")
            if os.path.exists(p):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                with open(p, "rb") as f: self.wfile.write(f.read())
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/mods":
            mods = scan_workshop_mods()
            threading.Thread(target=fetch_workshop_thumbnails, args=(mods,), daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(mods, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/thumb":
            pub_id = query.get("id", [""])[0]
            thumb_file = os.path.join(THUMBS_DIR, f"{pub_id}.jpg")
            if os.path.exists(thumb_file):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                with open(thumb_file, "rb") as f: self.wfile.write(f.read())
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/texture":
            name = query.get("name", [""])[0]
            png_file = os.path.join(THUMBS_DIR, f"{name}.png")
            if os.path.exists(png_file):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                with open(png_file, "rb") as f: self.wfile.write(f.read())
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/pbo-texture":
            pbo_path = query.get("pbo", [""])[0]
            paa_file = query.get("file", [""])[0]
            b = extract_file_across_mod(pbo_path, paa_file)
            if b:
                safe_n = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(os.path.basename(paa_file))[0])
                url = convert_paa_to_png(b, safe_n)
                if url and "?name=" in url:
                    cname = url.split("?name=")[1]
                    png_file = os.path.join(THUMBS_DIR, f"{cname}.png")
                    if os.path.exists(png_file):
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.end_headers()
                        with open(png_file, "rb") as f: self.wfile.write(f.read())
                        return
            self.send_response(404); self.end_headers()
            return

        elif path == "/api/local-image":
            img_path = query.get("path", [""])[0]
            if os.path.exists(img_path):
                ext = os.path.splitext(img_path)[1].lower()
                mime = "image/png" if ext == ".png" else "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.end_headers()
                with open(img_path, "rb") as f: self.wfile.write(f.read())
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/open-folder":
            folder = query.get("path", [""])[0]
            if os.path.exists(folder):
                os.startfile(folder)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/toggle":
            folder = query.get("folder", [""])[0]
            enabled = query.get("enabled", ["true"])[0] == "true"
            states = load_states()
            states[folder] = enabled
            save_states(states)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        elif path == "/api/toggle-all":
            val = query.get("val", ["true"])[0] == "true"
            states = load_states()
            for item in os.listdir(WORKSHOP_DIR):
                if item.startswith("@"): states[item] = val
            save_states(states)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        elif path == "/api/pbo-inspect":
            pbo_path = query.get("path", [""])[0]
            if os.path.exists(pbo_path):
                prefix, entries = get_pbo_details(pbo_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"prefix": prefix, "entries": entries}, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/pbo-unpack":
            pbo_path = query.get("path", [""])[0]
            mod_name = query.get("mod", ["mod"])[0]
            pbo_base = os.path.splitext(os.path.basename(pbo_path))[0]
            dest = os.path.join(EXTRACT_BASE, mod_name, pbo_base)
            if os.path.exists(pbo_path):
                unpack_single_pbo(pbo_path, dest)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "path": dest}, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404); self.end_headers()
            return

        elif path == "/api/p3d-data":
            pbo_path = query.get("pbo", [""])[0]
            model_file = query.get("file", [""])[0]
            hint_tex = query.get("tex", [""])[0]
            
            p3d_bytes = None
            if pbo_path and os.path.exists(pbo_path):
                p3d_bytes = extract_file_from_pbo(pbo_path, model_file)
            elif os.path.exists(model_file):
                with open(model_file, "rb") as f:
                    p3d_bytes = f.read()

            if not p3d_bytes or len(p3d_bytes) < 4:
                self.send_response(404); self.end_headers()
                return

            sig = p3d_bytes[:4]
            mlod_bytes = None
            mlod_path_saved = None
            is_debinarized = False

            if sig == b'MLOD':
                mlod_bytes = p3d_bytes
            elif sig == b'ODOL':
                safe_name = os.path.splitext(os.path.basename(model_file))[0]
                mlod_bytes, mlod_path_saved = debinarize_odol_bytes(p3d_bytes, safe_name)
                is_debinarized = True

            if mlod_bytes and mlod_bytes[:4] == b'MLOD':
                parsed = parse_mlod_bytes(mlod_bytes)
                parsed["filename"] = os.path.basename(model_file)
                parsed["model_file_rel"] = model_file
                parsed["pbo_path"] = pbo_path
                parsed["is_debinarized"] = is_debinarized
                parsed["mlod_file"] = mlod_path_saved
                
                # Multi-submesh Texture & Skin resolution
                resolve_submeshes_and_skins(pbo_path, model_file, hint_tex, p3d_bytes, parsed)

                # Attach config.bin metadata (class_name, gear_type, armor, mass, load, author)
                try:
                    if pbo_path and os.path.exists(pbo_path):
                        cfg_b = extract_file_from_pbo(pbo_path, "config.bin") or extract_file_from_pbo(pbo_path, "config.cpp")
                        if cfg_b:
                            pbo_nm = os.path.splitext(os.path.basename(pbo_path))[0]
                            mp = parse_config_model_mappings(cfg_b, pbo_name=pbo_nm)
                            cmeta = mp.get(os.path.basename(model_file).lower(), {})
                            if cmeta:
                                parsed["config_meta"] = cmeta
                except Exception:
                    pass
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(parsed, ensure_ascii=False).encode("utf-8"))
            elif sig == b'ODOL':
                version = struct.unpack('<I', p3d_bytes[4:8])[0] if len(p3d_bytes) >= 8 else 0
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "type": "ODOL_FAILED",
                    "version": version,
                    "filename": os.path.basename(model_file),
                    "size": len(p3d_bytes)
                }, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(400); self.end_headers()
            return

        elif path == "/api/send-to-blender":
            pbo_path = query.get("pbo", [""])[0]
            model_file = query.get("file", [""])[0]
            try:
                import importlib
                importlib.reload(ez_mod_core)
                manifest = ez_mod_core.prepare_model_for_blender_import(pbo_path=pbo_path, model_file=model_file)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "manifest": manifest}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/export-mlod":
            src = query.get("src", [""])[0]
            name = query.get("name", ["model"])[0]
            if os.path.exists(src):
                dest = os.path.join(EXTRACT_BASE, f"{name}_mlod.p3d")
                with open(src, "rb") as in_f, open(dest, "wb") as out_f:
                    out_f.write(in_f.read())
                os.startfile(os.path.dirname(dest))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "path": dest}).encode("utf-8"))
            else:
                self.send_response(404); self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arma 3 Workshop Mod Manager & 3D Inspector</title>
<script src="/api/three.js"></script>
<script src="/api/orbit-controls.js"></script>
<style>
  :root {
    --bg-base: #0b0f17;
    --bg-surface: #141a24;
    --bg-card: #18202d;
    --bg-card-hover: #1f2a3c;
    --border: #232d3f;
    --border-hover: #00ff88;
    --accent: #00ff88;
    --accent-cyan: #00f0ff;
    --text-main: #f0f4f8;
    --text-muted: #8b9bb4;
    --badge-bg: rgba(0, 240, 255, 0.12);
    --badge-color: #00f0ff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg-base);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    min-height: 100vh;
    padding: 16px 24px;
    user-select: none;
  }
  header {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 20px;
    margin-bottom: 20px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
  .title-group { display: flex; align-items: center; gap: 14px; }
  .app-icon {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, #00f0ff, #00ff88);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 24px; font-weight: bold; color: #0b0f17;
    box-shadow: 0 0 20px rgba(0,255,136,0.4);
  }
  .title-text h1 {
    font-size: 20px; font-weight: 700; letter-spacing: 0.5px;
    display: flex; align-items: center; gap: 8px;
  }
  .title-text .path-sub {
    font-size: 11px; color: var(--text-muted);
    font-family: Consolas, monospace; margin-top: 3px;
  }
  .stats-badges { display: flex; gap: 12px; }
  .stat-pill {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 14px; text-align: center;
  }
  .stat-pill .num { font-size: 15px; font-weight: 700; color: var(--accent); }
  .stat-pill .label { font-size: 10px; text-transform: uppercase; color: var(--text-muted); }
  .header-actions { display: flex; gap: 8px; }
  .btn {
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-main);
    padding: 8px 14px; border-radius: 8px;
    font-size: 12px; font-weight: 600; cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .btn:hover { border-color: var(--accent-cyan); color: var(--accent-cyan); transform: translateY(-1px); }
  .btn.btn-accent {
    background: linear-gradient(135deg, rgba(0,255,136,0.2), rgba(0,240,255,0.2));
    border-color: var(--accent); color: var(--accent);
  }
  .btn.btn-accent:hover {
    background: var(--accent); color: #0b0f17;
    box-shadow: 0 0 16px rgba(0,255,136,0.4);
  }
  .btn.btn-cyan {
    background: linear-gradient(135deg, rgba(0,240,255,0.2), rgba(0,180,255,0.2));
    border-color: var(--accent-cyan); color: var(--accent-cyan);
  }
  .btn.btn-cyan:hover {
    background: var(--accent-cyan); color: #0b0f17;
    box-shadow: 0 0 16px rgba(0,240,255,0.4);
  }

  .controls-bar {
    display: flex; flex-wrap: wrap; gap: 12px;
    align-items: center; justify-content: space-between;
    margin-bottom: 20px;
  }
  .search-box { position: relative; flex: 1; min-width: 280px; max-width: 480px; }
  .search-box input {
    width: 100%; background: var(--bg-surface);
    border: 1px solid var(--border); color: var(--text-main);
    padding: 10px 14px 10px 38px; border-radius: 10px;
    font-size: 13px; outline: none; transition: all 0.2s;
  }
  .search-box input:focus { border-color: var(--accent-cyan); box-shadow: 0 0 14px rgba(0,240,255,0.2); }
  .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 14px; }
  .filter-pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .filter-btn {
    background: var(--bg-surface); border: 1px solid var(--border);
    color: var(--text-muted); padding: 6px 14px; border-radius: 20px;
    font-size: 11px; font-weight: 600; cursor: pointer; transition: all 0.2s;
  }
  .filter-btn:hover { color: var(--text-main); border-color: #3b4960; }
  .filter-btn.active { background: rgba(0, 240, 255, 0.15); border-color: var(--accent-cyan); color: var(--accent-cyan); }
  .sort-select {
    background: var(--bg-surface); border: 1px solid var(--border);
    color: var(--text-main); padding: 8px 12px; border-radius: 8px;
    font-size: 12px; outline: none; cursor: pointer;
  }

  .mod-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
    gap: 16px;
  }
  .mod-card {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden; display: flex; flex-direction: column;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1); position: relative;
  }
  .mod-card:hover { border-color: #3f4e68; transform: translateY(-3px); box-shadow: 0 10px 24px rgba(0,0,0,0.5); }
  .mod-card.enabled { border-left: 3px solid var(--accent); }
  .mod-card.disabled { opacity: 0.55; border-left: 3px solid #444; }
  .mod-card.disabled:hover { opacity: 0.9; }

  .thumb-container {
    width: 100%; height: 145px; background: #0f141d;
    position: relative; overflow: hidden; display: flex;
    align-items: center; justify-content: center;
  }
  .thumb-img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s ease; }
  .mod-card:hover .thumb-img { transform: scale(1.05); }
  .thumb-fallback { color: var(--text-muted); font-size: 32px; opacity: 0.4; }
  .badge-cat {
    position: absolute; top: 8px; left: 8px;
    background: rgba(11, 15, 23, 0.85); backdrop-filter: blur(4px);
    border: 1px solid var(--badge-color); color: var(--badge-color);
    font-size: 9px; font-weight: 700; text-transform: uppercase;
    padding: 3px 8px; border-radius: 6px;
  }
  .badge-size {
    position: absolute; top: 8px; right: 8px;
    background: rgba(11, 15, 23, 0.85); backdrop-filter: blur(4px);
    border: 1px solid rgba(255,255,255,0.15); color: #ccc;
    font-size: 10px; font-weight: 600; padding: 3px 8px; border-radius: 6px;
    font-family: Consolas, monospace;
  }

  .card-body { padding: 12px 14px; display: flex; flex-direction: column; flex: 1; gap: 6px; }
  .mod-title { font-size: 14px; font-weight: 700; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mod-meta { font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; }
  .mod-meta .author { max-width: 140px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pbo-pill { font-size: 10px; background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px; color: #a0aec0; }

  .card-footer {
    padding: 10px 14px; border-top: 1px solid rgba(255,255,255,0.05);
    background: rgba(0,0,0,0.15); display: flex; align-items: center; justify-content: space-between;
  }
  .action-icons { display: flex; gap: 6px; }
  .icon-btn {
    width: 28px; height: 28px; border-radius: 6px;
    background: var(--bg-card); border: 1px solid var(--border);
    color: var(--text-muted); display: flex; align-items: center;
    justify-content: center; cursor: pointer; font-size: 12px; transition: all 0.2s;
  }
  .icon-btn:hover { color: var(--accent-cyan); border-color: var(--accent-cyan); background: var(--bg-card-hover); }

  .switch { position: relative; display: inline-block; width: 44px; height: 22px; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #2b3648; transition: .3s; border-radius: 22px;
  }
  .slider:before {
    position: absolute; content: ""; height: 16px; width: 16px;
    left: 3px; bottom: 3px; background-color: white; transition: .3s; border-radius: 50%;
  }
  input:checked + .slider { background-color: var(--accent); box-shadow: 0 0 10px rgba(0,255,136,0.4); }
  input:checked + .slider:before { transform: translateX(22px); background-color: #0b0f17; }

  /* Modals */
  .modal-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.8); backdrop-filter: blur(8px);
    display: none; align-items: center; justify-content: center; z-index: 1000;
  }
  .modal-content {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 14px; width: 90%; max-width: 860px; max-height: 88vh;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 20px 60px rgba(0,0,0,0.8);
  }
  .modal-header {
    padding: 16px 20px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center;
  }
  .modal-header h2 { font-size: 16px; color: var(--accent-cyan); }
  .modal-close { background: transparent; border: none; color: var(--text-muted); font-size: 22px; cursor: pointer; }
  .modal-body { padding: 16px 20px; overflow-y: auto; flex: 1; }

  .pbo-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .pbo-table th { text-align: left; color: var(--text-muted); padding: 8px; border-bottom: 1px solid var(--border); }
  .pbo-table td { padding: 8px 8px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
  .tag-mlod { color: #00ff88; background: rgba(0,255,136,0.1); padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }
  .tag-odol { color: #ffaa00; background: rgba(255,170,0,0.1); padding: 2px 6px; border-radius: 4px; font-size: 11px; }

  .friendly-title {
    color: var(--accent-cyan); font-weight: 600; font-size: 12px; display: block; margin-top: 2px;
  }
  .friendly-desc {
    color: #8b9bb4; font-size: 11px; margin-top: 1px; display: block;
  }

  /* 3D Inspector Modal */
  .inspector-modal { max-width: 1080px; width: 95%; height: 86vh; }
  .inspector-layout { display: flex; flex: 1; height: 100%; overflow: hidden; }
  .viewport-3d-box {
    flex: 1; position: relative; background: #07090e;
    overflow: hidden; border-right: 1px solid var(--border);
  }
  #canvas3D { width: 100%; height: 100%; display: block; }
  .viewport-overlay-controls {
    position: absolute; top: 12px; left: 12px;
    display: flex; gap: 8px; z-index: 10;
  }
  .v-btn {
    background: rgba(20, 26, 36, 0.85); backdrop-filter: blur(4px);
    border: 1px solid var(--border); color: #ccc; padding: 6px 12px;
    border-radius: 6px; font-size: 11px; cursor: pointer; font-weight: 600;
  }
  .v-btn:hover { border-color: var(--accent-cyan); color: var(--accent-cyan); }
  .v-btn.active { background: rgba(0,240,255,0.2); border-color: var(--accent-cyan); color: var(--accent-cyan); }

  .inspector-sidebar {
    width: 350px; background: var(--bg-surface);
    display: flex; flex-direction: column; padding: 16px; gap: 14px;
    overflow-y: auto;
  }
  .sidebar-section h3 {
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text-muted); margin-bottom: 8px;
  }
  .info-badge-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .info-box {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px;
  }
  .info-box .val { font-size: 14px; font-weight: 700; color: var(--accent); }
  .info-box .lbl { font-size: 10px; color: var(--text-muted); }
  .selection-list { display: flex; flex-wrap: wrap; gap: 4px; max-height: 180px; overflow-y: auto; }
  .sel-tag {
    background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1);
    color: #a0aec0; padding: 3px 8px; border-radius: 4px; font-size: 10px;
    font-family: Consolas, monospace;
  }
  .item-meta-banner {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px;
  }
  .item-meta-banner .item-title {
    font-size: 15px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 4px;
  }
  .item-meta-banner .item-desc {
    font-size: 11px; color: #cbd5e1; line-height: 1.4;
  }
  .debin-tag {
    display: inline-block; background: rgba(0,255,136,0.15); border: 1px solid var(--accent);
    color: var(--accent); font-size: 10px; font-weight: 700; padding: 2px 8px;
    border-radius: 4px; margin-top: 6px;
  }
</style>
</head>
<body>

<header>
  <div class="title-group">
    <div class="app-icon">⚔</div>
    <div class="title-text">
      <h1>Arma 3 Workshop Manager & 3D Inspector</h1>
      <div class="path-sub">Z:\\SteamLibrary\\steamapps\\common\\Arma 3\\!Workshop</div>
    </div>
  </div>
  
  <div class="stats-badges">
    <div class="stat-pill">
      <div class="num" id="stat-total">0</div>
      <div class="label">Всего модов</div>
    </div>
    <div class="stat-pill">
      <div class="num" id="stat-enabled" style="color:var(--accent)">0</div>
      <div class="label">Включено</div>
    </div>
    <div class="stat-pill">
      <div class="num" id="stat-size" style="color:var(--accent-cyan)">0 GB</div>
      <div class="label">Объем на диске</div>
    </div>
  </div>

  <div class="header-actions">
    <button class="btn" onclick="toggleAll(true)">✓ Включить все</button>
    <button class="btn" onclick="toggleAll(false)">✕ Отключить все</button>
    <button class="btn btn-accent" onclick="loadMods()">↻ Обновить</button>
  </div>
</header>

<div class="controls-bar">
  <div class="search-box">
    <span class="search-icon">🔍</span>
    <input type="text" id="searchInput" placeholder="Поиск по названию, ID, автору..." oninput="renderMods()">
  </div>

  <div class="filter-pills" id="categoryPills">
    <button class="filter-btn active" data-cat="all" onclick="setCategory('all')">Все</button>
    <button class="filter-btn" data-cat="gear" onclick="setCategory('gear')">Экипировка</button>
    <button class="filter-btn" data-cat="weapons" onclick="setCategory('weapons')">Оружие</button>
    <button class="filter-btn" data-cat="anims" onclick="setCategory('anims')">Анимации/Звуки</button>
    <button class="filter-btn" data-cat="maps" onclick="setCategory('maps')">Карты</button>
    <button class="filter-btn" data-cat="vehicles" onclick="setCategory('vehicles')">Техника</button>
    <button class="filter-btn" data-cat="core" onclick="setCategory('core')">Core / Системные</button>
    <button class="filter-btn" data-cat="enabled" onclick="setCategory('enabled')">Только включенные</button>
  </div>

  <select class="sort-select" id="sortSelect" onchange="renderMods()">
    <option value="name_asc">Сортировка: Имя (А-Я)</option>
    <option value="name_desc">Сортировка: Имя (Я-А)</option>
    <option value="size_desc">Размер (по убыванию)</option>
    <option value="size_asc">Размер (по возрастанию)</option>
    <option value="pbo_desc">Кол-во PBO</option>
  </select>
</div>

<div class="mod-grid" id="modGrid"></div>

<!-- Modal 1: PBO Inspector -->
<div class="modal-overlay" id="pboModal">
  <div class="modal-content">
    <div class="modal-header">
      <h2 id="modalModTitle">Содержимое мода</h2>
      <button class="modal-close" onclick="closeModal('pboModal')">✕</button>
    </div>
    <div class="modal-body" id="modalModBody"></div>
  </div>
</div>

<!-- Modal 2: 3D Model Inspector -->
<div class="modal-overlay" id="inspectorModal">
  <div class="modal-content inspector-modal">
    <div class="modal-header">
      <h2 id="inspectorTitle">👁 3D Инспектор модели</h2>
      <button class="modal-close" onclick="closeModal('inspectorModal')">✕</button>
    </div>
    <div class="inspector-layout">
      <!-- 3D Canvas Viewport -->
      <div class="viewport-3d-box" id="viewportBox">
        <div class="viewport-overlay-controls">
          <button class="v-btn" id="btnTexture" onclick="toggleTexture()">🎨 Текстура</button>
          <button class="v-btn" id="btnNormal" onclick="toggleNormal()">🗺 Рельеф</button>
          <button class="v-btn" id="btnWireframe" onclick="toggleWireframe()">🕸 Сетка</button>
          <button class="v-btn" id="btnNormals" onclick="toggleFlatShading()">💡 Освещение</button>
          <button class="v-btn" onclick="resetCamera()">🎯 Сброс вида</button>
        </div>
        <canvas id="canvas3D"></canvas>
      </div>

      <!-- Right Sidebar with metadata, LODs, Selections, Stats -->
      <div class="inspector-sidebar">
        <div class="item-meta-banner" id="itemBanner" style="display:none;">
          <div class="item-title" id="itemFriendlyName">Название предмета</div>
          <div class="item-desc" id="itemFriendlyDesc">Описание предмета в игре</div>
          <div class="debin-tag" id="debinTag" style="display:none;">⚡ 3D СЕТКА РАСПАКОВАНА (MLOD)</div>
        </div>

        <div class="sidebar-section" id="skinSection" style="display:none;">
          <h3>🎨 Скин / Камуфляж</h3>
          <select class="sort-select" style="width:100%;margin-bottom:8px;" id="skinSelect" onchange="switchSkin(this.value)"></select>
        </div>

        <div class="sidebar-section">
          <h3>Уровень детализации (LOD)</h3>
          <select class="sort-select" style="width:100%;margin-bottom:8px;" id="lodSelect" onchange="switchLOD(this.value)"></select>
        </div>

        <div class="sidebar-section">
          <h3>Геометрия модели</h3>
          <div class="info-badge-grid">
            <div class="info-box">
              <div class="val" id="statVerts">0</div>
              <div class="lbl">Вершин (Vertices)</div>
            </div>
            <div class="info-box">
              <div class="val" id="statFaces">0</div>
              <div class="lbl">Полигонов (Faces)</div>
            </div>
            <div class="info-box">
              <div class="val" id="statLODs">0</div>
              <div class="lbl">Всего LOD'ов</div>
            </div>
            <div class="info-box">
              <div class="val" id="statMass">0 kg</div>
              <div class="lbl">Масса (Mass)</div>
            </div>
          </div>
        </div>

        <div class="sidebar-section" id="ezModConfigSection" style="display:none;">
          <h3>⚙ Настройки предмета (EZ_MOD Config)</h3>
          <div class="info-badge-grid">
            <div class="info-box">
              <div class="val" id="cfgSlot" style="font-size:12px;color:var(--accent-cyan)">HEADGEAR</div>
              <div class="lbl">Слот в Арсенале</div>
            </div>
            <div class="info-box">
              <div class="val" id="cfgClass" style="font-size:11px;word-break:break-all;">class_id</div>
              <div class="lbl">ID Класса</div>
            </div>
            <div class="info-box">
              <div class="val" id="cfgArmorLoad">6 / 0</div>
              <div class="lbl">Броня / Вмест.</div>
            </div>
            <div class="info-box">
              <div class="val" id="cfgMassAuth">45</div>
              <div class="lbl">Масса (Config)</div>
            </div>
          </div>
        </div>

        <div class="sidebar-section" id="selectionsSection">
          <h3>Именованные выборки (Named Selections)</h3>
          <div class="selection-list" id="selectionsList"></div>
        </div>

        <div style="margin-top:auto;display:flex;flex-direction:column;gap:8px;">
          <button class="btn btn-accent" style="justify-content:center;padding:11px 14px;font-size:13px;" id="btnSendToBlender" onclick="sendCurrentModelToBlender(this)">📥 Импорт в Blender (со всеми настройками)</button>
          <button class="btn btn-cyan" style="justify-content:center;padding:8px 14px;" id="btnExportMLOD" onclick="exportCurrentMLOD()">💾 Сохранить чистый MLOD (.p3d)</button>
          <button class="btn" style="justify-content:center;" onclick="openCurrentInExplorer()">📂 Открыть папку с моделями</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let allMods = [];
let currentCategory = 'all';

let renderer, scene, camera, controls;
let currentMesh = null, currentWireMesh = null;
let currentModelData = null;
let loadedTexture = null, loadedNormalMap = null;
let showTexture = true, showNormal = true;
let isWireframe = false, isFlatShading = false;

async function loadMods() {
  try {
    const res = await fetch('/api/mods');
    allMods = await res.json();
    updateStats();
    renderMods();
  } catch (e) {
    console.error('Error loading mods:', e);
  }
}

function updateStats() {
  document.getElementById('stat-total').innerText = allMods.length;
  const enabledCount = allMods.filter(m => m.enabled).length;
  document.getElementById('stat-enabled').innerText = enabledCount;
  const totalBytes = allMods.reduce((acc, m) => acc + (m.size || 0), 0);
  document.getElementById('stat-size').innerText = (totalBytes / 1073741824).toFixed(1) + ' GB';
}

function setCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === cat);
  });
  renderMods();
}

function renderMods() {
  const query = document.getElementById('searchInput').value.toLowerCase().trim();
  const sortMode = document.getElementById('sortSelect').value;
  const grid = document.getElementById('modGrid');

  let filtered = allMods.filter(m => {
    if (currentCategory === 'enabled' && !m.enabled) return false;
    if (currentCategory !== 'all' && currentCategory !== 'enabled' && m.category !== currentCategory) return false;
    if (query) {
      const match = m.name.toLowerCase().includes(query) ||
                    m.folder.toLowerCase().includes(query) ||
                    m.author.toLowerCase().includes(query) ||
                    (m.pub_id && m.pub_id.includes(query));
      if (!match) return false;
    }
    return true;
  });

  filtered.sort((a, b) => {
    if (sortMode === 'name_asc') return a.name.localeCompare(b.name);
    if (sortMode === 'name_desc') return b.name.localeCompare(a.name);
    if (sortMode === 'size_desc') return b.size - a.size;
    if (sortMode === 'size_asc') return a.size - b.size;
    if (sortMode === 'pbo_desc') return b.pbo_count - a.pbo_count;
    return 0;
  });

  grid.innerHTML = filtered.map(m => {
    const thumb = m.thumb_url
      ? `<img class="thumb-img" src="${m.thumb_url}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='block';"><div class="thumb-fallback" style="display:none">📦</div>`
      : `<div class="thumb-fallback">📦</div>`;

    return `
      <div class="mod-card ${m.enabled ? 'enabled' : 'disabled'}" id="card-${m.folder}">
        <div class="thumb-container">
          ${thumb}
          <div class="badge-cat">${m.category_label}</div>
          <div class="badge-size">${m.size_formatted}</div>
        </div>
        
        <div class="card-body">
          <div class="mod-title" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</div>
          <div class="mod-meta">
            <span class="author" title="${escapeHtml(m.author)}">👤 ${escapeHtml(m.author)}</span>
            <span class="pbo-pill">${m.pbo_count} PBO</span>
          </div>
        </div>

        <div class="card-footer">
          <label class="switch">
            <input type="checkbox" ${m.enabled ? 'checked' : ''} onchange="toggleMod('${m.folder}', this.checked)">
            <span class="slider"></span>
          </label>
          
          <div class="action-icons">
            <button class="icon-btn" title="Открыть папку в проводнике" onclick="openFolder('${escapeJs(m.full_path)}')">📁</button>
            <button class="icon-btn" title="Просмотр PBO и 3D моделей" onclick="inspectModPBOs('${escapeJs(m.folder)}')">📦</button>
            ${m.pub_id && m.pub_id !== '0' ? `<button class="icon-btn" title="Страница в мастерской Steam" onclick="window.open('https://steamcommunity.com/sharedfiles/filedetails/?id=${m.pub_id}', '_blank')">🌐</button>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function toggleMod(folder, enabled) {
  const mod = allMods.find(m => m.folder === folder);
  if (mod) mod.enabled = enabled;
  const card = document.getElementById(`card-${folder}`);
  if (card) {
    card.classList.toggle('enabled', enabled);
    card.classList.toggle('disabled', !enabled);
  }
  updateStats();
  await fetch(`/api/toggle?folder=${encodeURIComponent(folder)}&enabled=${enabled}`);
}

async function toggleAll(val) {
  allMods.forEach(m => m.enabled = val);
  updateStats();
  renderMods();
  await fetch(`/api/toggle-all?val=${val}`);
}

function openFolder(path) {
  fetch(`/api/open-folder?path=${encodeURIComponent(path)}`);
}

function inspectModPBOs(folder) {
  const mod = allMods.find(m => m.folder === folder);
  if (!mod) return;
  document.getElementById('modalModTitle').innerText = `${mod.name} — Список PBO архивов`;
  
  const body = document.getElementById('modalModBody');
  if (!mod.pbo_list || mod.pbo_list.length === 0) {
    body.innerHTML = '<p style="color:#888;padding:20px 0;">В этом моде нет PBO архивов (или нестандартная структура папок).</p>';
  } else {
    body.innerHTML = `
      <table class="pbo-table">
        <thead>
          <tr>
            <th>Файл PBO</th>
            <th>Размер</th>
            <th style="text-align:right">Действия</th>
          </tr>
        </thead>
        <tbody>
          ${mod.pbo_list.map(p => {
            const pboPath = `${mod.full_path}\\\\addons\\\\${p.name}`;
            const sz = (p.size / 1048576).toFixed(1) + ' MB';
            return `
              <tr>
                <td><strong>${escapeHtml(p.name)}</strong></td>
                <td>${sz}</td>
                <td style="text-align:right">
                  <button class="btn" style="padding:4px 10px;font-size:11px;" onclick="inspectPboDetails('${escapeJs(pboPath)}')">🔍 Инспектировать</button>
                  <button class="btn btn-accent" style="padding:4px 10px;font-size:11px;" onclick="unpackPbo('${escapeJs(pboPath)}', '${escapeJs(mod.folder)}')">📂 Распаковать</button>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
      <div id="pboInspectionResults" style="margin-top:16px;"></div>
    `;
  }
  document.getElementById('pboModal').style.display = 'flex';
}

async function inspectPboDetails(pboPath) {
  const container = document.getElementById('pboInspectionResults');
  container.innerHTML = '<div style="color:var(--accent-cyan);padding:10px 0;">Анализ содержимого и расшифровка config.bin...</div>';
  try {
    const res = await fetch(`/api/pbo-inspect?path=${encodeURIComponent(pboPath)}`);
    const data = await res.json();
    container.innerHTML = `
      <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:10px;">
        <div style="font-weight:bold;margin-bottom:8px;color:var(--accent)">Префикс PBO: ${data.prefix || '(не указан)'} | Всего файлов: ${data.entries.length}</div>
        <div style="max-height:300px;overflow-y:auto;">
          <table class="pbo-table">
            <thead>
              <tr><th>Файл / Название в игре</th><th>Размер</th><th>Формат</th><th style="text-align:right">Действие</th></tr>
            </thead>
            <tbody>
              ${data.entries.map(e => {
                const isP3D = e.filename.toLowerCase().endsWith('.p3d');
                const slotBadge = e.gear_type ? `<span style="background:rgba(0,255,136,0.12);color:#00ff88;padding:1px 6px;border-radius:4px;font-size:10px;margin-right:6px;">${escapeHtml(e.gear_type)}</span>` : '';
                const clsBadge = e.class_name ? `<span style="color:#a0aec0;font-size:10px;font-family:Consolas,monospace;">[${escapeHtml(e.class_name)} | Масса: ${e.mass || 0} | Броня: ${e.armor || 0}]</span>` : '';
                const friendlyTitle = e.display_name ? `<span class="friendly-title">${slotBadge}🏷 ${escapeHtml(e.display_name)} ${clsBadge}</span>` : '';
                const friendlyDesc = e.description ? `<span class="friendly-desc">${escapeHtml(e.description)}</span>` : '';
                
                return `
                  <tr>
                    <td>
                      <div><code>${escapeHtml(e.filename)}</code></div>
                      ${friendlyTitle}
                      ${friendlyDesc}
                    </td>
                    <td>${(e.size / 1024).toFixed(1)} KB</td>
                    <td>${e.p3d_type.includes('MLOD') ? '<span class="tag-mlod">MLOD (Blender OK)</span>' : (e.p3d_type.includes('ODOL') ? '<span class="tag-odol">ODOL (Бинарник)</span>' : e.p3d_type)}</td>
                    <td style="text-align:right;white-space:nowrap;">
                      ${isP3D ? `
                        <button class="btn btn-cyan" style="padding:4px 9px;font-size:11px;margin-right:4px;" onclick="open3DViewer('${escapeJs(pboPath)}', '${escapeJs(e.filename)}', '${escapeJs(e.display_name || '')}', '${escapeJs(e.description || '')}', '${escapeJs(e.texture || '')}')">👁 3D</button>
                        <button class="btn btn-accent" style="padding:4px 9px;font-size:11px;" onclick="sendModelToBlender('${escapeJs(pboPath)}', '${escapeJs(e.filename)}', this)">📥 В Blender</button>
                      ` : ''}
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div style="color:red">Ошибка инспекции: ${err}</div>`;
  }
}

async function unpackPbo(pboPath, modFolder) {
  try {
    const res = await fetch(`/api/pbo-unpack?path=${encodeURIComponent(pboPath)}&mod=${encodeURIComponent(modFolder)}`);
    const data = await res.json();
    alert(`Успешно распаковано в:\n${data.path}\n\nТеперь можно открывать .p3d в Blender!`);
    openFolder(data.path);
  } catch (err) {
    alert('Ошибка распаковки: ' + err);
  }
}

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}

function initThree() {
  if (renderer) return;
  const canvas = document.getElementById('canvas3D');
  const container = document.getElementById('viewportBox');
  
  renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.setPixelRatio(window.devicePixelRatio);
  
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0e17);
  
  const grid = new THREE.GridHelper(10, 20, 0x00f0ff, 0x1f293d);
  grid.position.y = -0.01;
  scene.add(grid);

  const ambLight = new THREE.AmbientLight(0xffffff, 0.9);
  scene.add(ambLight);
  
  const hemiLight = new THREE.HemisphereLight(0xffffff, 0x334455, 0.6);
  scene.add(hemiLight);

  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.9);
  dirLight1.position.set(5, 10, 7);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0x70c0ff, 0.4);
  dirLight2.position.set(-5, -5, -5);
  scene.add(dirLight2);

  camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.01, 100);
  camera.position.set(1.5, 1.5, 2.5);
  
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;

  window.addEventListener('resize', () => {
    if (!renderer) return;
    const box = document.getElementById('viewportBox');
    camera.aspect = box.clientWidth / box.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(box.clientWidth, box.clientHeight);
  });

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();
}

async function open3DViewer(pboPath, filename, friendlyName, friendlyDesc, hintTex) {
  initThree();
  document.getElementById('inspectorModal').style.display = 'flex';
  document.getElementById('inspectorTitle').innerText = `👁 3D Инспектор: ${filename}`;
  
  const banner = document.getElementById('itemBanner');
  if (friendlyName) {
    banner.style.display = 'block';
    document.getElementById('itemFriendlyName').innerText = friendlyName;
    document.getElementById('itemFriendlyDesc').innerText = friendlyDesc || 'Элемент снаряжения/оружия Arma 3';
  } else {
    banner.style.display = 'none';
  }

  setTimeout(() => {
    const box = document.getElementById('viewportBox');
    camera.aspect = box.clientWidth / box.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(box.clientWidth, box.clientHeight);
  }, 100);

  if (currentMesh) { scene.remove(currentMesh); currentMesh = null; }
  if (currentWireMesh) { scene.remove(currentWireMesh); currentWireMesh = null; }
  loadedTexture = null;

  try {
    const res = await fetch(`/api/p3d-data?pbo=${encodeURIComponent(pboPath)}&file=${encodeURIComponent(filename)}&tex=${encodeURIComponent(hintTex || '')}`);
    const data = await res.json();
    currentModelData = data;
    currentModelData.pbo_path = pboPath;
    currentModelData.model_file_rel = filename;

    const cfgSec = document.getElementById('ezModConfigSection');
    if (data.config_meta && cfgSec) {
      cfgSec.style.display = 'block';
      document.getElementById('cfgSlot').innerText = data.config_meta.gear_type || 'HEADGEAR';
      document.getElementById('cfgClass').innerText = data.config_meta.class_name || '-';
      document.getElementById('cfgArmorLoad').innerText = `${data.config_meta.armor || 0} / ${data.config_meta.maximumLoad || 0}`;
      document.getElementById('cfgMassAuth').innerText = `${data.config_meta.mass || 0} (${data.config_meta.author || 'Mod'})`;
    } else if (cfgSec) {
      cfgSec.style.display = 'none';
    }
    
    if (data.texture_url) {
      document.getElementById('btnTexture').style.display = 'inline-block';
      document.getElementById('btnTexture').classList.add('active');
      showTexture = true;
    } else {
      document.getElementById('btnTexture').style.display = 'none';
      showTexture = false;
    }

    if (data.normal_url) {
      document.getElementById('btnNormal').style.display = 'inline-block';
      document.getElementById('btnNormal').classList.add('active');
      showNormal = true;
    } else {
      document.getElementById('btnNormal').style.display = 'none';
      showNormal = false;
    }

    const skinSection = document.getElementById('skinSection');
    const skinSelect = document.getElementById('skinSelect');
    if (data.skin_presets && data.skin_presets.length > 1) {
      skinSection.style.display = 'block';
      skinSelect.innerHTML = data.skin_presets.map((p, idx) => `<option value="preset:${idx}">${escapeHtml(p.name)}</option>`).join('');
    } else if (data.textures && data.textures.length > 1) {
      skinSection.style.display = 'block';
      skinSelect.innerHTML = data.textures.map(t => `<option value="${escapeHtml(t.url)}">${escapeHtml(t.name)}</option>`).join('');
    } else {
      skinSection.style.display = 'none';
    }

    if (data.lods && data.lods.length > 0) {
      showRealMeshView(data);
    } else {
      alert('Не удалось прочитать полигональную сетку');
    }
  } catch (err) {
    alert('Ошибка загрузки 3D модели: ' + err);
  }
}

function showRealMeshView(data) {
  const debinTag = document.getElementById('debinTag');
  if (data.is_debinarized) {
    debinTag.style.display = 'inline-block';
  } else {
    debinTag.style.display = 'none';
  }

  document.getElementById('selectionsSection').style.display = 'block';
  
  const lodSelect = document.getElementById('lodSelect');
  lodSelect.innerHTML = data.lods.map((l, i) => `<option value="${i}">${escapeHtml(l.label)} (${l.vertex_count} верт.)</option>`).join('');
  
  document.getElementById('statLODs').innerText = data.total_lods;
  switchLOD(0);
}

function getMeshMaterials() {
  if (!currentMesh || !currentMesh.material) return [];
  return Array.isArray(currentMesh.material) ? currentMesh.material : [currentMesh.material];
}

function switchLOD(index) {
  if (!currentModelData || !currentModelData.lods) return;
  const lod = currentModelData.lods[index];
  if (!lod) return;

  document.getElementById('statVerts').innerText = lod.vertex_count;
  document.getElementById('statFaces').innerText = lod.face_count;
  document.getElementById('statMass').innerText = (lod.mass || 0) + ' kg';

  const selList = document.getElementById('selectionsList');
  if (lod.selections && lod.selections.length > 0) {
    selList.innerHTML = lod.selections.map(s => `<span class="sel-tag">${escapeHtml(s)}</span>`).join('');
  } else {
    selList.innerHTML = '<span style="color:#666;font-size:10px;">Служебный или коллизионный LOD</span>';
  }

  if (currentMesh) { scene.remove(currentMesh); currentMesh = null; }
  if (currentWireMesh) { scene.remove(currentWireMesh); currentWireMesh = null; }

  if (lod.flat_positions && lod.flat_positions.length > 0) {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.Float32BufferAttribute(lod.flat_positions, 3));
    geom.setAttribute('normal', new THREE.Float32BufferAttribute(lod.flat_normals, 3));
    if (lod.flat_uvs && lod.flat_uvs.length > 0) {
      geom.setAttribute('uv', new THREE.Float32BufferAttribute(lod.flat_uvs, 2));
    }

    const loader = new THREE.TextureLoader();
    const subs = (lod.submeshes && lod.submeshes.length > 0) ? lod.submeshes : [{
      start: 0,
      count: lod.flat_positions.length / 3,
      diffuse_url: currentModelData.texture_url,
      normal_url: currentModelData.normal_url,
      slot_id: 'camo'
    }];

    const mats = [];
    subs.forEach((sub, idx) => {
      geom.addGroup(sub.start, sub.count, idx);
      const isGlassSub = (sub.tex || '').toLowerCase().includes('_ca.paa');
      const m = new THREE.MeshStandardMaterial({
        color: 0x5a6e8c,
        metalness: isGlassSub ? 0.05 : 0.15,
        roughness: isGlassSub ? 0.12 : 0.75,
        transparent: isGlassSub,
        depthWrite: !isGlassSub,
        flatShading: isFlatShading,
        side: THREE.DoubleSide
      });
      m.userData = {
        slot_id: sub.slot_id || 'camo',
        defaultDiffUrl: sub.diffuse_url || null,
        defaultNormUrl: sub.normal_url || null,
        loadedDiff: null,
        loadedNorm: null
      };

      if (sub.diffuse_url) {
        loader.load(sub.diffuse_url, (tex) => {
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.RepeatWrapping;
          m.userData.loadedDiff = tex;
          if (showTexture) {
            m.map = tex;
            m.color.setHex(0xffffff);
            m.needsUpdate = true;
          }
        });
      }
      if (sub.normal_url) {
        loader.load(sub.normal_url, (ntex) => {
          ntex.wrapS = THREE.RepeatWrapping;
          ntex.wrapT = THREE.RepeatWrapping;
          m.userData.loadedNorm = ntex;
          if (showNormal) {
            m.normalMap = ntex;
            m.normalScale = new THREE.Vector2(1, -1);
            m.needsUpdate = true;
          }
        });
      }
      mats.push(m);
    });

    currentMesh = new THREE.Mesh(geom, mats);
    scene.add(currentMesh);

    const wireMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff, wireframe: true });
    currentWireMesh = new THREE.Mesh(geom, wireMat);
    currentWireMesh.visible = isWireframe;
    scene.add(currentWireMesh);
  }

  resetCamera();
}

function toggleTexture() {
  showTexture = !showTexture;
  document.getElementById('btnTexture').classList.toggle('active', showTexture);
  getMeshMaterials().forEach(m => {
    const tex = m.userData ? m.userData.loadedDiff : loadedTexture;
    m.map = (showTexture && tex) ? tex : null;
    m.color.setHex((showTexture && tex) ? 0xffffff : 0x5a6e8c);
    m.needsUpdate = true;
  });
}

function toggleNormal() {
  showNormal = !showNormal;
  document.getElementById('btnNormal').classList.toggle('active', showNormal);
  getMeshMaterials().forEach(m => {
    const ntex = m.userData ? m.userData.loadedNorm : loadedNormalMap;
    m.normalMap = (showNormal && ntex) ? ntex : null;
    m.normalScale = new THREE.Vector2(1, -1);
    m.needsUpdate = true;
  });
}

function switchSkin(val) {
  if (!val) return;
  const loader = new THREE.TextureLoader();
  if (val.startsWith('preset:') && currentModelData && currentModelData.skin_presets) {
    const idx = parseInt(val.split(':')[1], 10);
    const preset = currentModelData.skin_presets[idx];
    if (!preset || !preset.slot_urls) return;
    getMeshMaterials().forEach(m => {
      const slot = (m.userData && m.userData.slot_id) ? m.userData.slot_id : 'camo';
      if (slot.startsWith('fixed:')) return; // keep attachment/flashlight textures intact
      const newUrl = preset.slot_urls[slot] || m.userData.defaultDiffUrl;
      if (newUrl) {
        loader.load(newUrl, (tex) => {
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.RepeatWrapping;
          m.userData.loadedDiff = tex;
          if (showTexture) {
            m.map = tex;
            m.color.setHex(0xffffff);
            m.needsUpdate = true;
          }
        });
      }
    });
  } else {
    loader.load(val, (tex) => {
      tex.wrapS = THREE.RepeatWrapping;
      tex.wrapT = THREE.RepeatWrapping;
      loadedTexture = tex;
      getMeshMaterials().forEach(m => {
        const slot = (m.userData && m.userData.slot_id) ? m.userData.slot_id : 'camo';
        if (slot.startsWith('fixed:')) return;
        if (m.userData) m.userData.loadedDiff = tex;
        if (showTexture) {
          m.map = tex;
          m.color.setHex(0xffffff);
          m.needsUpdate = true;
        }
      });
    });
  }
}

function toggleWireframe() {
  isWireframe = !isWireframe;
  document.getElementById('btnWireframe').classList.toggle('active', isWireframe);
  if (currentWireMesh) currentWireMesh.visible = isWireframe;
}

function toggleFlatShading() {
  isFlatShading = !isFlatShading;
  document.getElementById('btnNormals').classList.toggle('active', !isFlatShading);
  getMeshMaterials().forEach(m => {
    m.flatShading = isFlatShading;
    m.needsUpdate = true;
  });
}

function resetCamera() {
  if (!currentMesh) {
    camera.position.set(1.5, 1.5, 2.5);
    controls.target.set(0, 0, 0);
    return;
  }
  const box = new THREE.Box3().setFromObject(currentMesh);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 0.4);
  
  camera.position.set(center.x + maxDim * 1.5, center.y + maxDim * 1.2, center.z + maxDim * 1.8);
  controls.target.copy(center);
  camera.lookAt(center);
}

async function sendModelToBlender(pboPath, modelFile, btnEl) {
  const origHtml = btnEl ? btnEl.innerHTML : '';
  if (btnEl) {
    btnEl.innerHTML = '⏳ Готовим пакет...';
    btnEl.disabled = true;
  }
  try {
    const res = await fetch(`/api/send-to-blender?pbo=${encodeURIComponent(pboPath)}&file=${encodeURIComponent(modelFile)}`);
    const data = await res.json();
    if (data.status === 'ok' && data.manifest) {
      const m = data.manifest;
      if (btnEl) {
        btnEl.innerHTML = '✅ В очереди Blender!';
        setTimeout(() => { btnEl.innerHTML = origHtml; btnEl.disabled = false; }, 2500);
      }
      alert(
        `✅ Модель «${m.item_name}» готова к импорту в Blender!\n\n` +
        `• Слот: ${m.gear_type}\n` +
        `• ID Класса: ${m.class_slug}\n` +
        `• Автор: ${m.author_name}\n` +
        `• Броня: ${m.armor_val} | Масса: ${m.mass_val} | Вмест.: ${m.load_val}\n` +
        `• Материалов (с текстурами CO + NOHQ): ${m.material_slots.length}\n\n` +
        `👉 Перейди в Blender -> панель EZ_MOD и нажми кнопку:\n«📥 Импорт выбранной модели»!`
      );
    } else {
      throw new Error(data.error || 'Неизвестная ошибка');
    }
  } catch (err) {
    if (btnEl) {
      btnEl.innerHTML = origHtml;
      btnEl.disabled = false;
    }
    alert('Ошибка подготовки модели для Blender: ' + err);
  }
}

async function sendCurrentModelToBlender(btnEl) {
  if (!currentModelData || !currentModelData.model_file_rel) {
    alert('Сначала выберите модель!');
    return;
  }
  await sendModelToBlender(currentModelData.pbo_path || '', currentModelData.model_file_rel, btnEl);
}

async function exportCurrentMLOD() {
  if (!currentModelData || !currentModelData.mlod_file) {
    alert('Эта модель уже в формате MLOD или еще не дебинаризована.');
    return;
  }
  try {
    const res = await fetch(`/api/export-mlod?src=${encodeURIComponent(currentModelData.mlod_file)}&name=${encodeURIComponent(currentModelData.filename.replace('.p3d',''))}`);
    const data = await res.json();
    alert(`Модель успешно экспортирована в MLOD P3D:\n${data.path}\n\nТеперь открывайте её в Blender через ArmaToolbox!`);
  } catch (err) {
    alert('Ошибка экспорта: ' + err);
  }
}

function openCurrentInExplorer() {
  openFolder('Z:\\\\aarma\\\\extracted');
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escapeJs(str) {
  return String(str).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
}

loadMods();
</script>
</body>
</html>
"""

def main():
    port = 48123
    server = ThreadingHTTPServer(("127.0.0.1", port), ModManagerHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"==================================================")
    print(f"  Arma 3 Workshop Mod Manager & 3D Inspector")
    print(f"  Работает на: {url}")
    print(f"==================================================")
    
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")

if __name__ == "__main__":
    main()
