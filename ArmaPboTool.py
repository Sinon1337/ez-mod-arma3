import os
import sys
import struct
import hashlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- PBO Format Implementation ---

def read_cstring(f):
    chars = []
    while True:
        c = f.read(1)
        if not c or c == b'\x00':
            break
        chars.append(c)
    return b''.join(chars).decode('latin1', errors='replace')

def inspect_pbo(pbo_path):
    """Returns (prefix, entries) where entry is dict(filename, size, timestamp, p3d_type)"""
    with open(pbo_path, 'rb') as f:
        # First entry (header)
        fn = read_cstring(f)
        magic, orig_sz, res, ts, data_sz = struct.unpack('<4sIIII', f.read(20))
        props = {}
        while True:
            k = read_cstring(f)
            if not k:
                break
            v = read_cstring(f)
            props[k] = v
        
        prefix = props.get('prefix', '')
        
        entries = []
        while True:
            fn = read_cstring(f)
            magic, orig_sz, res, ts, data_sz = struct.unpack('<IIIII', f.read(20))
            if not fn and data_sz == 0:
                break
            entries.append({
                'filename': fn,
                'data_size': data_sz,
                'timestamp': ts,
                'p3d_type': '-'
            })
            
        # Detect P3D types by peeking into file data
        current_offset = f.tell()
        for item in entries:
            sz = item['data_size']
            if item['filename'].lower().endswith('.p3d') and sz >= 4:
                pos = f.tell()
                head4 = f.read(4)
                if head4 == b'MLOD':
                    item['p3d_type'] = 'MLOD (Blender OK)'
                elif head4 == b'ODOL':
                    item['p3d_type'] = 'ODOL (Бинаризован)'
                else:
                    item['p3d_type'] = head4.decode('ascii', errors='replace')
                f.seek(pos + sz)
            else:
                f.seek(sz, os.SEEK_CUR)
                
    return prefix, entries

def extract_pbo(pbo_path, output_dir, selected_files=None, progress_callback=None):
    os.makedirs(output_dir, exist_ok=True)
    with open(pbo_path, 'rb') as f:
        # Header
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
            if not fn and data_sz == 0:
                break
            entries.append((fn, data_sz))
            
        total = len(entries)
        for idx, (fn, sz) in enumerate(entries):
            data = f.read(sz)
            if selected_files is None or fn in selected_files:
                target_path = os.path.join(output_dir, fn.replace('/', os.sep).replace('\\', os.sep))
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, 'wb') as out_f:
                    out_f.write(data)
            if progress_callback:
                progress_callback(idx + 1, total, fn)

def pack_pbo(source_dir, output_pbo, prefix=None, progress_callback=None):
    source_dir = os.path.normpath(source_dir)
    if not prefix:
        prefix = os.path.basename(source_dir)
        
    files = []
    for root, _, filenames in os.walk(source_dir):
        for fn in filenames:
            full_path = os.path.join(root, fn)
            rel_path = os.path.relpath(full_path, source_dir).replace('/', '\\')
            files.append((rel_path, full_path))
            
    header_data = bytearray()
    # First entry (signature Vers)
    header_data.extend(b'\x00')
    header_data.extend(struct.pack('<4sIIII', b'sreV', 0, 0, 0, 0))
    if prefix:
        header_data.extend(b'prefix\x00' + prefix.encode('utf-8') + b'\x00')
    header_data.extend(b'\x00') # end of properties
    
    # File headers
    for rel_path, full_path in files:
        sz = os.path.getsize(full_path)
        mtime = int(os.path.getmtime(full_path))
        header_data.extend(rel_path.encode('latin1', errors='replace') + b'\x00')
        header_data.extend(struct.pack('<IIIII', 0, sz, 0, mtime, sz))
        
    # End of header block
    header_data.extend(b'\x00')
    header_data.extend(struct.pack('<IIIII', 0, 0, 0, 0, 0))
    
    os.makedirs(os.path.dirname(os.path.abspath(output_pbo)), exist_ok=True)
    hasher = hashlib.sha1()
    hasher.update(header_data)
    
    total = len(files)
    with open(output_pbo, 'wb') as out_f:
        out_f.write(header_data)
        for idx, (rel_path, full_path) in enumerate(files):
            with open(full_path, 'rb') as in_f:
                chunk = in_f.read()
                out_f.write(chunk)
                hasher.update(chunk)
            if progress_callback:
                progress_callback(idx + 1, total, rel_path)
        # 21-byte checksum: 0x00 followed by 20 bytes SHA-1
        out_f.write(b'\x00' + hasher.digest())

# --- GUI Application ---

class PboToolGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Arma 3 PBO Tool — распаковка и упаковка")
        self.root.geometry("860x580")
        self.root.minsize(700, 480)
        
        # Style
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=8)
        
        self.build_unpack_tab()
        self.build_pack_tab()
        
    def build_unpack_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=" Распаковка PBO ")
        
        # Top input row
        f_top = ttk.LabelFrame(tab, text="Файл PBO", padding=8)
        f_top.pack(fill='x', pady=4)
        
        self.unpack_pbo_path = tk.StringVar()
        entry_pbo = ttk.Entry(f_top, textvariable=self.unpack_pbo_path, font=('Segoe UI', 9))
        entry_pbo.pack(side='left', fill='x', expand=True, padx=4)
        
        btn_browse = ttk.Button(f_top, text="Выбрать PBO...", command=self.on_browse_unpack_pbo)
        btn_browse.pack(side='left', padx=4)
        
        # Prefix info
        self.lbl_prefix = ttk.Label(tab, text="Префикс мода: -", font=('Segoe UI', 9, 'bold'))
        self.lbl_prefix.pack(anchor='w', padx=6, pady=2)
        
        # Treeview for contents
        f_tree = ttk.Frame(tab)
        f_tree.pack(fill='both', expand=True, pady=6)
        
        columns = ('name', 'size', 'type')
        self.tree = ttk.Treeview(f_tree, columns=columns, show='headings', selectmode='extended')
        self.tree.heading('name', text='Имя файла / путь внутри PBO')
        self.tree.heading('size', text='Размер')
        self.tree.heading('type', text='Формат P3D / тип')
        self.tree.column('name', width=450)
        self.tree.column('size', width=110, anchor='e')
        self.tree.column('type', width=180, anchor='center')
        
        scroll_y = ttk.Scrollbar(f_tree, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        
        self.tree.pack(side='left', fill='both', expand=True)
        scroll_y.pack(side='right', fill='y')
        
        # Destination & Action row
        f_dest = ttk.LabelFrame(tab, text="Куда распаковать", padding=8)
        f_dest.pack(fill='x', pady=4)
        
        self.unpack_dest_path = tk.StringVar()
        entry_dest = ttk.Entry(f_dest, textvariable=self.unpack_dest_path, font=('Segoe UI', 9))
        entry_dest.pack(side='left', fill='x', expand=True, padx=4)
        
        btn_dest = ttk.Button(f_dest, text="Папка...", command=self.on_browse_unpack_dest)
        btn_dest.pack(side='left', padx=4)
        
        # Action Buttons
        f_acts = ttk.Frame(tab)
        f_acts.pack(fill='x', pady=4)
        
        self.btn_extract_sel = ttk.Button(f_acts, text="Распаковать только выделенные", command=self.on_extract_selected)
        self.btn_extract_sel.pack(side='left', padx=4)
        
        self.btn_extract_all = ttk.Button(f_acts, text="Распаковать ВСЁ", command=self.on_extract_all)
        self.btn_extract_all.pack(side='right', padx=4)
        
        self.lbl_unpack_status = ttk.Label(tab, text="Готов к работе", font=('Segoe UI', 8))
        self.lbl_unpack_status.pack(anchor='w', padx=6)
        
    def build_pack_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=" Сборка (Упаковка) PBO ")
        
        f_src = ttk.LabelFrame(tab, text="Папка с вашим модом (исходники)", padding=8)
        f_src.pack(fill='x', pady=6)
        
        self.pack_src_path = tk.StringVar()
        entry_src = ttk.Entry(f_src, textvariable=self.pack_src_path, font=('Segoe UI', 9))
        entry_src.pack(side='left', fill='x', expand=True, padx=4)
        
        btn_src = ttk.Button(f_src, text="Выбрать папку...", command=self.on_browse_pack_src)
        btn_src.pack(side='left', padx=4)
        
        f_pref = ttk.LabelFrame(tab, text="Префикс мода (Prefix / Tag)", padding=8)
        f_pref.pack(fill='x', pady=6)
        self.pack_prefix = tk.StringVar()
        entry_pref = ttk.Entry(f_pref, textvariable=self.pack_prefix, font=('Segoe UI', 9))
        entry_pref.pack(fill='x', padx=4)
        lbl_hint = ttk.Label(f_pref, text="Например: my_vest_mod (если оставить пустым, будет взято имя папки)", font=('Segoe UI', 8), foreground='#555')
        lbl_hint.pack(anchor='w', padx=4, pady=2)
        
        f_out = ttk.LabelFrame(tab, text="Итоговый файл .pbo", padding=8)
        f_out.pack(fill='x', pady=6)
        self.pack_out_pbo = tk.StringVar()
        entry_out = ttk.Entry(f_out, textvariable=self.pack_out_pbo, font=('Segoe UI', 9))
        entry_out.pack(side='left', fill='x', expand=True, padx=4)
        btn_out = ttk.Button(f_out, text="Куда сохранить...", command=self.on_browse_pack_out)
        btn_out.pack(side='left', padx=4)
        
        f_btn = ttk.Frame(tab, padding=12)
        f_btn.pack(fill='x', pady=10)
        
        self.btn_pack = ttk.Button(f_btn, text="📦 СОБРАТЬ В PBO", command=self.on_start_pack)
        self.btn_pack.pack(fill='x', ipady=6)
        
        self.lbl_pack_status = ttk.Label(tab, text="", font=('Segoe UI', 9))
        self.lbl_pack_status.pack(pady=4)

    def on_browse_unpack_pbo(self):
        fn = filedialog.askopenfilename(filetypes=[("Arma PBO file", "*.pbo"), ("All files", "*.*")])
        if fn:
            self.unpack_pbo_path.set(fn)
            base = os.path.splitext(fn)[0]
            self.unpack_dest_path.set(base)
            self.load_pbo_contents(fn)
            
    def load_pbo_contents(self, fn):
        self.tree.delete(*self.tree.get_children())
        try:
            prefix, entries = inspect_pbo(fn)
            self.lbl_prefix.config(text=f"Префикс мода (prefix): {prefix if prefix else '(не указан)'}")
            for item in entries:
                sz_str = f"{item['data_size'] / 1024:.1f} KB" if item['data_size'] < 1048576 else f"{item['data_size'] / 1048576:.2f} MB"
                self.tree.insert('', 'end', values=(item['filename'], sz_str, item['p3d_type']))
            self.lbl_unpack_status.config(text=f"Найдено файлов: {len(entries)}")
        except Exception as e:
            messagebox.showerror("Ошибка чтения PBO", str(e))

    def on_browse_unpack_dest(self):
        d = filedialog.askdirectory()
        if d:
            self.unpack_dest_path.set(d)
            
    def on_extract_all(self):
        pbo = self.unpack_pbo_path.get()
        dest = self.unpack_dest_path.get()
        if not pbo or not os.path.exists(pbo):
            messagebox.showwarning("Внимание", "Выберите файл .pbo для распаковки!")
            return
        if not dest:
            messagebox.showwarning("Внимание", "Укажите папку для распаковки!")
            return
        try:
            extract_pbo(pbo, dest)
            messagebox.showinfo("Успешно", f"Все файлы успешно распакованы в:\n{dest}")
        except Exception as e:
            messagebox.showerror("Ошибка распаковки", str(e))
            
    def on_extract_selected(self):
        pbo = self.unpack_pbo_path.get()
        dest = self.unpack_dest_path.get()
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Сначала выделите один или несколько файлов в списке!")
            return
        selected_files = [self.tree.item(i)['values'][0] for i in sel]
        try:
            extract_pbo(pbo, dest, selected_files=selected_files)
            messagebox.showinfo("Успешно", f"Извлечено файлов: {len(selected_files)}\nВ папку: {dest}")
        except Exception as e:
            messagebox.showerror("Ошибка распаковки", str(e))

    def on_browse_pack_src(self):
        d = filedialog.askdirectory()
        if d:
            self.pack_src_path.set(d)
            base_name = os.path.basename(os.path.normpath(d))
            self.pack_prefix.set(base_name)
            self.pack_out_pbo.set(os.path.join(os.path.dirname(os.path.normpath(d)), f"{base_name}.pbo"))

    def on_browse_pack_out(self):
        fn = filedialog.asksaveasfilename(defaultextension=".pbo", filetypes=[("Arma PBO file", "*.pbo")])
        if fn:
            self.pack_out_pbo.set(fn)
            
    def on_start_pack(self):
        src = self.pack_src_path.get()
        out_pbo = self.pack_out_pbo.get()
        pref = self.pack_prefix.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning("Внимание", "Выберите существующую папку для сборки!")
            return
        if not out_pbo:
            messagebox.showwarning("Внимание", "Укажите имя и путь выходного .pbo файла!")
            return
        try:
            pack_pbo(src, out_pbo, prefix=pref)
            messagebox.showinfo("Готово!", f"PBO успешно создан:\n{out_pbo}")
        except Exception as e:
            messagebox.showerror("Ошибка сборки", str(e))

def main():
    # Drag & Drop CLI handling
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if os.path.isfile(target) and target.lower().endswith('.pbo'):
            out_dir = os.path.splitext(target)[0]
            print(f"Распаковка {target} -> {out_dir} ...")
            extract_pbo(target, out_dir)
            print("Готово!")
            return
        elif os.path.isdir(target):
            out_pbo = target.rstrip('\\/ ') + ".pbo"
            print(f"Сборка {target} -> {out_pbo} ...")
            pack_pbo(target, out_pbo)
            print("Готово!")
            return

    root = tk.Tk()
    app = PboToolGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
