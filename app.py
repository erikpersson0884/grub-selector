#!/usr/bin/env python3
"""GRUB Theme Picker - pick a theme from your grub themes folder, apply it, reboot.

Run as a normal user:  python3 app.py
The GUI asks for your password (via pkexec) only when applying.
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

# --- distro detection (same logic as your installer) ------------------------
if shutil.which("grub2-mkconfig"):
    GRUB_DIR, MKCONFIG = "/boot/grub2", "grub2-mkconfig"
elif shutil.which("grub-mkconfig"):
    GRUB_DIR, MKCONFIG = "/boot/grub", "grub-mkconfig"
else:
    GRUB_DIR, MKCONFIG = None, None

THEMES_DIR = f"{GRUB_DIR}/themes" if GRUB_DIR else ""
DEFAULTS = "/etc/default/grub"
BACKUP = DEFAULTS + ".bak-themepicker"


def list_themes():
    if not os.path.isdir(THEMES_DIR):
        return []
    return sorted(
        d for d in os.listdir(THEMES_DIR)
        if os.path.isfile(os.path.join(THEMES_DIR, d, "theme.txt"))
    )


def current_theme():
    try:
        with open(DEFAULTS) as f:
            for line in f:
                m = re.match(r'^GRUB_THEME=["\']?([^"\']+)["\']?', line.strip())
                if m:
                    return os.path.basename(os.path.dirname(m.group(1)))
    except OSError:
        pass
    return None


# --- root part: runs via pkexec ---------------------------------------------
def apply_theme(name):
    if os.geteuid() != 0:
        sys.exit("Must run as root")
    if not MKCONFIG:
        sys.exit("Could not find grub-mkconfig / grub2-mkconfig")

    if not os.path.exists(BACKUP):
        shutil.copy(DEFAULTS, BACKUP)

    def set_var(key, val):
        with open(DEFAULTS) as f:
            lines = [l for l in f if not re.match(rf"^[#\s]*{key}=", l)]
        if val is not None:
            lines.append(f'{key}="{val}"\n')
        with open(DEFAULTS, "w") as f:
            f.writelines(lines)

    if name == "__none__":
        set_var("GRUB_THEME", None)
    else:
        theme_txt = os.path.join(THEMES_DIR, name, "theme.txt")
        if not os.path.isfile(theme_txt):
            sys.exit(f"{theme_txt} not found")
        set_var("GRUB_THEME", theme_txt)
        set_var("GRUB_TERMINAL_OUTPUT", "gfxterm")
        set_var("GRUB_TIMEOUT_STYLE", "menu")

    r = subprocess.run([MKCONFIG, "-o", f"{GRUB_DIR}/grub.cfg"],
                       capture_output=True, text=True)
    print(r.stdout + r.stderr)
    sys.exit(r.returncode)


# --- adding themes ----------------------------------------------------------
ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")


def _clean_name(name):
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .") or "theme"


def _archive_stem(path):
    base = os.path.basename(path)
    for ext in ARCHIVE_EXTS:
        if base.lower().endswith(ext):
            return base[: -len(ext)]
    return os.path.splitext(base)[0]


def _shallowest_theme_dir(root_dir):
    best = None
    for dirpath, _dirs, files in os.walk(root_dir):
        if "theme.txt" in files:
            depth = dirpath.count(os.sep)
            if best is None or depth < best[0]:
                best = (depth, dirpath)
    return best[1] if best else None


def find_theme_name(src):
    """Folder name the theme will get when installed, or None if no theme.txt is found."""
    if os.path.isdir(src):
        d = _shallowest_theme_dir(src)
        return _clean_name(os.path.basename(os.path.abspath(d))) if d else None
    try:
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
        elif tarfile.is_tarfile(src):
            with tarfile.open(src) as t:
                names = [m.name for m in t.getmembers()]
        else:
            return None
    except Exception:
        return None
    norm = [re.sub(r"^(\./)+", "", n.replace("\\", "/")) for n in names]
    cands = [n for n in norm if n.split("/")[-1] == "theme.txt"]
    if not cands:
        return None
    best = min(cands, key=lambda n: n.count("/"))
    parent = best.rsplit("/", 1)[0] if "/" in best else ""
    return _clean_name(os.path.basename(parent) if parent else _archive_stem(src))


def _safe_member(name):
    parts = name.replace("\\", "/").split("/")
    return not os.path.isabs(name) and ".." not in parts


def _extract(src, dest):
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            if not all(_safe_member(n) for n in z.namelist()):
                sys.exit("Archive contains unsafe paths")
            z.extractall(dest)
    elif tarfile.is_tarfile(src):
        with tarfile.open(src) as t:
            members = [m for m in t.getmembers()
                       if _safe_member(m.name) and (m.isfile() or m.isdir())]
            try:
                t.extractall(dest, members=members, filter="data")
            except TypeError:  # older Python without extraction filters
                t.extractall(dest, members=members)
    else:
        sys.exit("Unsupported file type (use .zip or .tar.gz)")


def install_theme(src, replace=False):
    """Copy a theme folder or archive into the GRUB themes folder. Runs as root."""
    if os.geteuid() != 0:
        sys.exit("Must run as root")
    if not GRUB_DIR:
        sys.exit("Could not find GRUB (grub-mkconfig / grub2-mkconfig)")
    name = find_theme_name(src)
    if not name:
        sys.exit("No theme.txt found")
    dest = os.path.join(THEMES_DIR, name)
    if os.path.exists(dest) and not replace:
        sys.exit(3)
    tmp = None
    try:
        root_dir = src
        if not os.path.isdir(src):
            tmp = tempfile.mkdtemp(prefix="grub-theme-")
            _extract(src, tmp)
            root_dir = tmp
        theme_dir = _shallowest_theme_dir(root_dir)
        if not theme_dir:
            sys.exit("No theme.txt found")
        os.makedirs(THEMES_DIR, exist_ok=True)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(
            theme_dir, dest, symlinks=True,
            ignore=lambda d, names: [n for n in names if os.path.islink(os.path.join(d, n))])
        for dp, dns, fns in os.walk(dest):  # make it readable for the (non-root) GUI
            os.chmod(dp, 0o755)
            for fn in fns:
                os.chmod(os.path.join(dp, fn), 0o644)
        print(name)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# --- GUI --------------------------------------------------------------------
def gui():
    import colorsys
    import threading
    import tkinter as tk
    import tkinter.font as tkfont
    import zlib
    from tkinter import filedialog, messagebox

    BG, CARD, HOVER = "#1e1e2e", "#2a2b3d", "#34364d"
    ACCENT, ACCENT_HI = "#cba6f7", "#d9bdfa"
    TEXT, MUTED, SOFT = "#e6e6f0", "#8b8da6", "#c9cadb"
    GREEN, GREEN_BG = "#a6e3a1", "#233a2e"
    RED, RED_BG = "#f38ba8", "#3a2430"
    SW_W, SW_H = 72, 44

    root = tk.Tk(className="Grub-selector")  # lets the desktop match the window to its launcher
    root.title("GRUB Theme Picker")
    root.geometry("420x560")
    root.minsize(380, 460)
    root.configure(bg=BG)

    avail = set(tkfont.families())
    fam = next((f for f in ("Inter", "Cantarell", "Noto Sans", "Segoe UI", "Helvetica")
                if f in avail), "TkDefaultFont")
    f_title, f_body = (fam, 18, "bold"), (fam, 11)
    f_small, f_tiny, f_btn = (fam, 9), (fam, 8, "bold"), (fam, 11, "bold")

    state = {"current": current_theme(), "selected": None, "busy": False}
    themes = list_themes()
    if state["current"] in themes:
        state["selected"] = state["current"]

    # ---- theme previews: background image if we can load it, else a gradient
    def read_txt(name):
        try:
            with open(os.path.join(THEMES_DIR, name, "theme.txt"), errors="ignore") as f:
                return f.read()
        except OSError:
            return ""

    def theme_colors(name):
        h = (zlib.crc32(name.encode()) % 360) / 360.0
        def hx(l, s):
            r, g, b = colorsys.hls_to_rgb(h, l, s)
            return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
        c1, c2 = hx(0.14, 0.45), hx(0.62, 0.65)
        m = re.search(r'desktop-color:\s*"(#[0-9a-fA-F]{6})"', read_txt(name))
        return (m.group(1) if m else c1), c2

    def load_image(name, w, h):
        m = re.search(r'desktop-image:\s*"([^"]+)"', read_txt(name))
        if not m:
            return None
        path = os.path.join(THEMES_DIR, name, m.group(1))
        if not os.path.isfile(path):
            return None
        try:  # Pillow (optional): any format, nicely cropped
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGB")
            s = max(w / im.width, h / im.height)
            im = im.resize((max(w, int(im.width * s)), max(h, int(im.height * s))))
            l, t = (im.width - w) // 2, (im.height - h) // 2
            return ImageTk.PhotoImage(im.crop((l, t, l + w, t + h)))
        except Exception:
            pass
        if path.lower().endswith(".png"):  # plain tkinter: PNG only
            try:
                p = tk.PhotoImage(file=path)
                f = max(1, min(p.width() // w, p.height() // h))
                return p.subsample(f) if f > 1 else p
            except Exception:
                pass
        return None

    def swatch(parent, name, w=SW_W, h=SW_H):
        c = tk.Canvas(parent, width=w, height=h, bg=CARD, highlightthickness=0, bd=0)
        img = load_image(name, w, h) if w >= SW_W else None
        if img:
            c.create_image(w // 2, h // 2, image=img)
            c.img = img  # keep a reference
            return c
        a, b = theme_colors(name)
        ar, ag, ab = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        br, bg_, bb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        for x in range(w):
            t = x / max(1, w - 1)
            col = "#%02x%02x%02x" % (int(ar + (br - ar) * t), int(ag + (bg_ - ag) * t),
                                     int(ab + (bb - ab) * t))
            c.create_line(x, 0, x, h, fill=col)
        return c

    # ---- header: title left, current-theme pill right
    hdr = tk.Frame(root, bg=BG)
    hdr.pack(fill="x", padx=24, pady=(24, 14))
    hdr.columnconfigure(0, weight=1)
    left = tk.Frame(hdr, bg=BG)
    left.grid(row=0, column=0, sticky="w")
    tk.Label(left, text="GRUB Themes", font=f_title, bg=BG, fg=TEXT).pack(anchor="w")
    tk.Label(left, text="Choose what shows at boot", font=f_small, bg=BG,
             fg=MUTED).pack(anchor="w", pady=(2, 0))
    pill = tk.Frame(hdr, bg=CARD)
    pill.grid(row=0, column=1, sticky="e")

    def refresh_pill():
        for w in pill.winfo_children():
            w.destroy()
        cur = state["current"]
        if not cur:
            tk.Label(pill, text="default theme", font=f_small, bg=CARD, fg=SOFT,
                     padx=12, pady=6).pack()
            return
        sw = swatch(pill, cur, 16, 16)
        sw.pack(side="left", padx=(8, 0), pady=6)
        tk.Label(pill, text=cur, font=f_small, bg=CARD, fg=SOFT,
                 padx=8, pady=6).pack(side="left")

    # ---- toast (feedback banner)
    toast = tk.Frame(root, bg=GREEN_BG)
    toast_lbl = tk.Label(toast, text="", font=(fam, 10), bg=GREEN_BG, fg=GREEN,
                         anchor="w", justify="left", wraplength=330, padx=14, pady=10)
    toast_lbl.pack(fill="x")

    # ---- scrollable list
    wrap = tk.Frame(root, bg=BG)
    wrap.pack(fill="both", expand=True, padx=(24, 12), pady=4)
    canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
    sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview, width=8,
                      bg=BG, troughcolor=BG, bd=0, highlightthickness=0, relief="flat")
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=BG)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width - 12))

    def wheel(e):
        canvas.yview_scroll(-1 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 1, "units")
    for ev in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
        root.bind_all(ev, wheel)

    rows = {}

    def paint(name):
        r = rows[name]
        sel = state["selected"] == name
        bg = HOVER if (sel or r["hover"]) else CARD
        r["frame"].configure(bg=bg, highlightbackground=ACCENT if sel else bg)
        r["bar"].configure(bg=ACCENT if sel else bg)
        r["name"].configure(bg=bg, font=(fam, 11, "bold") if sel else f_body)
        r["badge"].configure(bg=bg)

    def select(name):
        if state["busy"]:
            return
        old = state["selected"]
        state["selected"] = name
        if old in rows:
            paint(old)
        paint(name)
        refresh_button()

    def build_rows():
        for w in inner.winfo_children():
            w.destroy()
        rows.clear()
        if not themes:
            tk.Label(inner, text=f"No themes found in\n{THEMES_DIR}", font=f_body,
                     bg=BG, fg=MUTED, justify="left").pack(anchor="w", pady=20)
        for name in themes:
            fr = tk.Frame(inner, bg=CARD, highlightthickness=1,
                          highlightbackground=CARD, cursor="hand2")
            fr.pack(fill="x", pady=5)
            bar = tk.Frame(fr, width=4, bg=CARD)
            bar.pack(side="left", fill="y")
            sw = swatch(fr, name)
            sw.pack(side="left", padx=(12, 0), pady=10)
            nm = tk.Label(fr, text=name, font=f_body, bg=CARD, fg=TEXT, anchor="w", padx=14)
            nm.pack(side="left", fill="x", expand=True)
            badge = tk.Label(fr, text="ACTIVE" if name == state["current"] else "",
                             font=f_tiny, bg=CARD, fg=GREEN, padx=12)
            badge.pack(side="right")
            rows[name] = {"frame": fr, "bar": bar, "name": nm, "badge": badge,
                          "sw": sw, "hover": False}

            def enter(_e, n=name):
                rows[n]["hover"] = True; paint(n)
            def leave(_e, n=name):
                rows[n]["hover"] = False; paint(n)
            for w in (fr, bar, sw, nm, badge):
                w.bind("<Button-1>", lambda _e, n=name: select(n))
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
            paint(name)
        add_row()

    # ---- footer: Apply (+ Reboot after a successful apply)
    foot = tk.Frame(root, bg=BG)
    foot.pack(fill="x", padx=24, pady=(10, 22))
    foot.columnconfigure(0, weight=1)

    def make_btn(parent, text, bg, fg, hi, cmd):
        b = tk.Label(parent, text=text, font=f_btn, bg=bg, fg=fg, pady=11, cursor="hand2")
        b._bg, b._hi, b._on, b._fg = bg, hi, True, fg
        b.bind("<Enter>", lambda _e: b.configure(bg=b._hi) if b._on else None)
        b.bind("<Leave>", lambda _e: b.configure(bg=b._bg) if b._on else None)
        b.bind("<Button-1>", lambda _e: cmd() if b._on else None)
        return b

    apply_btn = make_btn(foot, "Apply theme", ACCENT, BG, ACCENT_HI, lambda: on_apply())
    apply_btn.grid(row=0, column=0, sticky="ew")
    reboot_btn = make_btn(foot, "Reboot now", CARD, TEXT, HOVER, lambda: on_reboot())
    reboot_btn.configure(padx=20)

    def set_btn(b, enabled, text=None):
        b._on = enabled
        b.configure(bg=b._bg if enabled else CARD, fg=b._fg if enabled else MUTED,
                    cursor="hand2" if enabled else "arrow")
        if text:
            b.configure(text=text)

    def show_toast(msg, kind="ok"):
        bg, fg = {"ok": (GREEN_BG, GREEN), "err": (RED_BG, RED), "info": (CARD, SOFT)}[kind]
        toast.configure(bg=bg)
        toast_lbl.configure(text=msg, bg=bg, fg=fg)
        toast.pack(fill="x", padx=24, pady=(0, 8), before=wrap)

    def hide_toast():
        toast.pack_forget()

    def refresh_button():
        ok = bool(state["selected"]) and state["selected"] != state["current"] \
            and not state["busy"]
        set_btn(apply_btn, ok)

    def on_apply():
        name = state["selected"]
        if not name or state["busy"]:
            return
        state["busy"] = True
        set_btn(apply_btn, False, "Applying...")
        show_toast("Waiting for authentication...", "info")
        reboot_btn.grid_remove()
        result = {}

        def work():
            try:
                result["r"] = subprocess.run(
                    ["pkexec", sys.executable, os.path.abspath(__file__), "--apply", name],
                    capture_output=True, text=True)
            except FileNotFoundError:
                result["err"] = "pkexec not found - install polkit."

        def poll():
            if t.is_alive():
                root.after(100, poll)
                return
            state["busy"] = False
            set_btn(apply_btn, True, "Apply theme")
            r = result.get("r")
            if "err" in result:
                show_toast(result["err"], "err")
            elif r.returncode == 0:
                state["current"] = name
                build_rows()
                refresh_pill()
                show_toast(f"✓  '{name}' applied. Reboot to see it.", "ok")
                reboot_btn.grid(row=0, column=1, padx=(10, 0))
            elif r.returncode in (126, 127):
                hide_toast()
            else:
                show_toast("Failed - see details.", "err")
                messagebox.showerror("Failed", (r.stdout + r.stderr)[-1500:])
            refresh_button()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        root.after(100, poll)

    def on_reboot():
        if messagebox.askyesno("Reboot", "Reboot now?"):
            subprocess.Popen(["systemctl", "reboot"])

    # ---- adding a theme (archive or folder)
    def add_row():
        fr = tk.Frame(inner, bg=BG)
        fr.pack(fill="x", pady=(12, 4))
        tk.Label(fr, text="Add a theme (.zip, .tar.gz or folder)", font=f_small,
                 bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 6))
        btns = tk.Frame(fr, bg=BG)
        btns.pack(fill="x")
        btns.columnconfigure((0, 1), weight=1, uniform="add")
        for i, (label, kind) in enumerate((("+  Archive", "archive"), ("+  Folder", "folder"))):
            b = make_btn(btns, label, CARD, SOFT, HOVER, lambda k=kind: on_add(k))
            b.configure(font=f_small, pady=9)
            b.grid(row=0, column=i, sticky="ew", padx=(0, 5) if i == 0 else (5, 0))

    def run_priv(args, done):
        result = {}

        def work():
            try:
                result["r"] = subprocess.run(
                    ["pkexec", sys.executable, os.path.abspath(__file__)] + args,
                    capture_output=True, text=True)
            except FileNotFoundError:
                result["err"] = "pkexec not found - install polkit."

        def poll():
            if t.is_alive():
                root.after(100, poll)
            else:
                done(result)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        root.after(100, poll)

    def on_add(kind):
        if state["busy"]:
            return
        if kind == "archive":
            src = filedialog.askopenfilename(
                title="Choose a theme archive",
                filetypes=[("Theme archives", "*.zip *.tar *.tar.gz *.tgz *.tar.xz *.txz *.tar.bz2 *.tbz2"),
                           ("All files", "*")])
        else:
            src = filedialog.askdirectory(title="Choose a theme folder", mustexist=True)
        if not src:
            return
        name = find_theme_name(src)
        if not name:
            show_toast(f"No theme.txt found in that {kind}.", "err")
            return
        args = ["--install", os.path.abspath(src)]
        if name in themes:
            if not messagebox.askyesno("Replace theme", f"'{name}' is already installed. Replace it?"):
                return
            args.append("--replace")
        state["busy"] = True
        refresh_button()
        reboot_btn.grid_remove()
        show_toast(f"Installing '{name}'... (enter your password)", "info")

        def done(res):
            state["busy"] = False
            r = res.get("r")
            if "err" in res:
                show_toast(res["err"], "err")
            elif r.returncode == 0:
                themes[:] = list_themes()
                if name in themes:
                    state["selected"] = name
                build_rows()
                show_toast(f"✓  '{name}' added. Select it and apply to use it.", "ok")
            elif r.returncode in (126, 127):
                hide_toast()
            else:
                show_toast("Install failed - see details.", "err")
                messagebox.showerror("Failed", (r.stdout + r.stderr)[-1500:])
            refresh_button()

        run_priv(args, done)

    refresh_pill()
    build_rows()
    refresh_button()
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--apply":
        apply_theme(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "--install":
        install_theme(sys.argv[2], "--replace" in sys.argv[3:])
    else:
        gui()