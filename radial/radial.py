#!/usr/bin/env python3
"""
radial.py — hierarchical radial launcher

Living audio-reactive core + real functionality:
  - Type to search: this ring, the whole menu, then every installed app
  - Tab / arrows / wheel cycle, right-click goes back, Esc steps out
  - Icons, live on/off state dots for toggles, per-item hints
  - Themeable from menu.json, errors shown on screen instead of failing silently
  - Mouse-wheel volume control on the core

    radial.py            toggle
    radial.py --daemon   start hidden (autostart this, then bind a key to radial.py)
    radial.py --quit     stop the daemon

menu.json (next to this script) is re-read every time the menu opens.
Top level: "theme" {accent, accent2, deep, surface, text, ink, branch, dim,
           dimOpacity, font, iconFont, albumColors}, "icons", "status" (true/false),
           "terminal" ("foot"), "position" ("center" | "cursor"),
           "screen" ("cursor" | "primary"), "search_url" (web fallback, %s = query)
Per item:  "label", "cmd" | "children", plus optional
           "icon"  glyph drawn above the label (needs a Nerd Font)
           "desc"  hint shown while hovering
           "color" hex hover colour          "keep"  stay open after running
           "term"  run cmd inside the terminal
           "state" shell test, exit 0 = on, shows a green/grey dot
           "dynamic" fills the ring when opened: "builtin:wifi", "builtin:clipboard",
                     "builtin:recent[:~/dir]" or a shell command printing one
                     "label<TAB>command" per line (or a JSON array of items)

Search understands fuzzy typing ("frfx"), ranks what you use most first, does
maths ("2+2*8", "sqrt(2)") and falls back to a web search. Press and drag from
the core, then release over a direction to flick-select. The core takes on the
colours of the album art that is playing.
"""
import ast
import json
import math
import operator
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage
from PySide6.QtNetwork import QLocalServer, QLocalSocket, QNetworkAccessManager, QNetworkRequest
from PySide6.QtQml import QQmlApplicationEngine

SOCKET_NAME = "radial-menu"
# Everything lives next to this script (e.g. ~/Projects/radial/)
SCRIPT_DIR = Path(__file__).resolve().parent
MENU_PATH = SCRIPT_DIR / "menu.json"
MAX_RESULTS = 8
USAGE_PATH = SCRIPT_DIR / "usage.json"
DEFAULT_SEARCH_URL = "http://localhost:8888/search?q=%s"
MAX_DYNAMIC = 12

DEFAULT_MENU = json.loads(r'''{
  "terminal": "foot",
  "icons": true,
  "status": true,
  "position": "center",
  "screen": "cursor",
  "search_url": "http://localhost:8888/search?q=%s",
  "theme": {
    "accent": "#88C0D0",
    "accent2": "#8FBCBB",
    "deep": "#5E81AC",
    "surface": "#2A303A",
    "text": "#ECEFF4",
    "ink": "#12141A",
    "branch": "#A3BE8C",
    "dim": "#0E1116",
    "dimOpacity": 0.8,
    "font": "Monocraft",
    "iconFont": "Symbols Nerd Font",
    "albumColors": true
  },
  "items": [
    {
      "label": "Launcher",
      "icon": "\uf135",
      "desc": "Search and open any app",
      "cmd": "gtk-launch otter-launcher"
    },
    {
      "label": "Apps",
      "icon": "\uf009",
      "children": [
        {
          "label": "Browser",
          "icon": "\uf0ac",
          "cmd": "brave"
        },
        {
          "label": "Terminal",
          "icon": "\uf120",
          "cmd": "foot"
        },
        {
          "label": "Files",
          "icon": "\uf07c",
          "cmd": "dolphin"
        },
        {
          "label": "Editor",
          "icon": "\uf044",
          "cmd": "kate"
        }
      ]
    },
    {
      "label": "System",
      "icon": "\uf108",
      "children": [
        {
          "label": "Settings",
          "icon": "\uf013",
          "cmd": "systemsettings"
        },
        {
          "label": "Wallpapers",
          "icon": "\uf03e",
          "cmd": "wallpaper-picker"
        },
        {
          "label": "Toggles",
          "icon": "\uf205",
          "children": [
            {
              "label": "Wi-Fi",
              "icon": "\uf1eb",
              "keep": true,
              "state": "nmcli radio wifi | grep -q enabled",
              "cmd": "if nmcli radio wifi | grep -q enabled; then nmcli radio wifi off; else nmcli radio wifi on; fi"
            },
            {
              "label": "Bluetooth",
              "icon": "\uf293",
              "keep": true,
              "state": "bluetoothctl show | grep -q 'Powered: yes'",
              "cmd": "if bluetoothctl show | grep -q 'Powered: yes'; then bluetoothctl power off; else bluetoothctl power on; fi"
            },
            {
              "label": "Mic mute",
              "icon": "\uf131",
              "keep": true,
              "state": "wpctl get-volume @DEFAULT_AUDIO_SOURCE@ | grep -q MUTED",
              "cmd": "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"
            },
            {
              "label": "Sound mute",
              "icon": "\uf026",
              "keep": true,
              "state": "wpctl get-volume @DEFAULT_AUDIO_SINK@ | grep -q MUTED",
              "cmd": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"
            },
            {
              "label": "Networks",
              "icon": "\uf012",
              "desc": "Nearby Wi-Fi networks",
              "dynamic": "builtin:wifi"
            }
          ]
        },
        {
          "label": "Lock",
          "icon": "\uf023",
          "cmd": "loginctl lock-session"
        },
        {
          "label": "Sleep",
          "icon": "\uf186",
          "cmd": "systemctl suspend"
        },
        {
          "label": "Power",
          "icon": "\uf011",
          "children": [
            {
              "label": "Log out",
              "icon": "\uf08b",
              "cmd": "qdbus6 org.kde.Shutdown /Shutdown logout"
            },
            {
              "label": "Reboot",
              "icon": "\uf021",
              "cmd": "systemctl reboot"
            },
            {
              "label": "Shut down",
              "icon": "\uf011",
              "cmd": "systemctl poweroff"
            }
          ]
        }
      ]
    },
    {
      "label": "Media",
      "icon": "\uf001",
      "children": [
        {
          "label": "Play / pause",
          "icon": "\uf04b",
          "cmd": "playerctl play-pause"
        },
        {
          "label": "Next",
          "icon": "\uf051",
          "cmd": "playerctl next"
        },
        {
          "label": "Previous",
          "icon": "\uf048",
          "cmd": "playerctl previous"
        },
        {
          "label": "Mute",
          "icon": "\uf026",
          "state": "wpctl get-volume @DEFAULT_AUDIO_SINK@ | grep -q MUTED",
          "cmd": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"
        }
      ]
    },
    {
      "label": "Folders",
      "icon": "\uf07b",
      "children": [
        {
          "label": "Home",
          "icon": "\uf015",
          "cmd": "dolphin ~"
        },
        {
          "label": "Downloads",
          "icon": "\uf019",
          "cmd": "dolphin ~/Downloads"
        },
        {
          "label": "Projects",
          "icon": "\uf121",
          "cmd": "dolphin ~/Projects"
        },
        {
          "label": "Config",
          "icon": "\uf013",
          "cmd": "dolphin ~/.config"
        },
        {
          "label": "Recent",
          "icon": "\uf017",
          "desc": "Latest downloads",
          "dynamic": "builtin:recent:~/Downloads"
        }
      ]
    }
  ]
}''')


def clean_items(items, depth=0):
    """Drop malformed entries so a typo in menu.json can never break the menu."""
    out = []
    if not isinstance(items, list) or depth > 6:
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", "")).strip()
        if not label:
            continue
        node = dict(it)
        node["label"] = label
        if "children" in node:
            node["children"] = clean_items(node.get("children"), depth + 1)
            if not node["children"]:
                node.pop("children")
                if not node.get("cmd") and not node.get("dynamic"):
                    continue
        elif not node.get("cmd") and not node.get("dynamic"):
            continue
        out.append(node)
    return out


CAVA_CONFIG = """[general]
bars = 56
framerate = 60
[input]
method = pipewire
source = auto
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 100
bar_delimiter = 59
frame_delimiter = 10
[smoothing]
noise_reduction = 38
"""



def fuzzy_score(q, text):
    """Lower is better, None = no match: prefix < word start < substring < subsequence."""
    q, t = q.lower(), text.lower()
    if not q:
        return None
    if t.startswith(q):
        return 0.0
    i = t.find(q)
    if i >= 0:
        return 1.0 if t[i - 1] in " -_.:/(" else 2.0
    if len(q) < 2:
        return None
    pos, first, last, n = 0, -1, -1, 0
    for ch in q:
        if ch == " ":
            continue
        j = t.find(ch, pos)
        if j < 0:
            return None
        if first < 0:
            first = j
        last, pos, n = j, j + 1, n + 1
    if first < 0:
        return None
    return 3.0 + min(last - first + 1 - n, 40) / 50


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log10, "ln": math.log, "abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval(expr):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 1000:
                raise ValueError("exponent too large")
            return _BIN_OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in _FUNCS and len(n.args) == 1 and not n.keywords:
            return _FUNCS[n.func.id](ev(n.args[0]))
        raise ValueError("unsupported")
    return ev(ast.parse(expr, mode="eval"))


def calc_text(text):
    """Result of a maths expression as a string, or "" if text isn't one."""
    body = text.strip().lower().replace("^", "**")
    if not body or not re.fullmatch(r"[0-9a-z+\-*/().%, ]+", body):
        return ""
    if not (re.search(r"[0-9)a-z]\s*(\*\*|[+\-*/%])", body)
            or re.search(r"(sqrt|sin|cos|tan|log|ln|abs|round)\s*\(", body)):
        return ""
    try:
        v = safe_eval(body)
    except Exception:
        return ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else f"{v:.10g}"
    return str(v)


def parse_volume(text):
    m = re.search(r"Volume:\s*([0-9.]+)", text)
    if not m:
        return None
    return {"pct": round(float(m.group(1)) * 100), "muted": "MUTED" in text}


def dominant_color(img):
    """Most prominent vivid colour of an image as '#rrggbb' ('' if it is grey)."""
    if img.isNull():
        return ""
    img = img.scaled(32, 32, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    buckets = {}
    for y in range(32):
        for x in range(32):
            c = QColor(img.pixel(x, y))
            h, s, v, _a = c.getHsvF()
            if s < 0.28 or v < 0.35 or h < 0:
                continue
            w = s * v
            b = buckets.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0.0])
            b[0] += w
            b[1] += w * c.red()
            b[2] += w * c.green()
            b[3] += w * c.blue()
    if not buckets:
        return ""
    w, r, g, b = max(buckets.values(), key=lambda x: x[0])
    c = QColor(int(r / w), int(g / w), int(b / w))
    h, s, v, _a = c.getHsvF()
    return QColor.fromHsvF(h, min(s, 0.85), max(v, 0.78)).name()


def split_nmcli(line):
    return [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]


def truncate(text, n=22):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def clean_dynamic(items):
    out = clean_items(items if isinstance(items, list) else [])
    out = [it for it in out if it.get("cmd")][:MAX_DYNAMIC]
    for it in out:
        it["label"] = truncate(it["label"])
    return out


def load_usage():
    try:
        data = json.loads(USAGE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class Backend(QObject):
    toggleRequested = Signal()
    binsChanged = Signal()
    trackChanged = Signal()
    statesChanged = Signal()
    volumeChanged = Signal()
    artColorChanged = Signal()
    dynamicReady = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._bins = []
        self._cava = None
        self._buf = ""
        self._track = {}
        self._pc = None
        self._states = {}
        self._apps = None
        self._terminal = "foot"
        self._search_url = DEFAULT_SEARCH_URL
        self._usage = load_usage()
        self._volume = {"pct": 0, "muted": False}
        self._vp = None
        self._art_url = ""
        self._art_color = ""
        self._nam = None

    # ---- installed apps (for type-to-search) -------------------------------
    def _scan_apps(self):
        if self._apps is not None:
            return self._apps
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        dirs = [data_home / "applications"]
        for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
            if d:
                dirs.append(Path(d) / "applications")
        apps, seen = [], set()
        for d in dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.desktop")):
                if f.name in seen:
                    continue
                seen.add(f.name)
                entry, in_entry = {}, False
                try:
                    lines = f.read_text(errors="ignore").splitlines()
                except OSError:
                    continue
                for line in lines:
                    line = line.strip()
                    if line.startswith("["):
                        if in_entry:
                            break
                        in_entry = line == "[Desktop Entry]"
                        continue
                    if in_entry and "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k = k.strip()
                        if k in ("Name", "GenericName", "Keywords", "Exec", "Type",
                                 "NoDisplay", "Hidden", "Terminal") and k not in entry:
                            entry[k] = v.strip()
                if entry.get("Type") != "Application" or not entry.get("Name") or not entry.get("Exec"):
                    continue
                if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
                    continue
                apps.append({
                    "id": f.name,
                    "name": entry["Name"],
                    "lname": entry["Name"].lower(),
                    "gen": entry.get("GenericName", "").lower(),
                    "kw": entry.get("Keywords", "").lower(),
                    "term": entry.get("Terminal", "").lower() == "true",
                    "exec": entry["Exec"],
                })
        self._apps = apps
        return apps

    def _app_cmd(self, a):
        if a["term"]:
            exe = re.sub(r"%[a-zA-Z%]", "", a["exec"]).strip()
            return f"{self._terminal} -e {exe}"
        return f"gtk-launch {shlex.quote(a['id'])}"

    # ---- usage ranking ("frecency") -----------------------------------------
    def _use_score(self, cmd):
        u = self._usage.get(cmd)
        if not u:
            return 0.0
        age = max(0.0, (time.time() - u.get("t", 0)) / 86400)
        return u.get("n", 0) * (0.5 ** (age / 14))

    def _record_use(self, cmd):
        u = self._usage.get(cmd) or {"n": 0, "t": 0}
        u["n"] = u.get("n", 0) + 1
        u["t"] = int(time.time())
        self._usage[cmd] = u
        if len(self._usage) > 300:
            keep = sorted(self._usage, key=self._use_score, reverse=True)[:250]
            self._usage = {k: self._usage[k] for k in keep}
        try:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            USAGE_PATH.write_text(json.dumps(self._usage))
        except OSError:
            pass

    # ---- search: apps, maths, web ---------------------------------------------
    @Slot(str, int, result="QVariantList")
    def searchApps(self, query, limit):
        q = query.lower().strip()
        if not q or limit <= 0:
            return []
        scored = []
        for a in self._scan_apps():
            s = fuzzy_score(q, a["lname"])
            if s is None and (q in a["gen"] or q in a["kw"]):
                s = 4.0
            if s is None:
                continue
            cmd = self._app_cmd(a)
            scored.append((int(s), -self._use_score(cmd), s, len(a["lname"]), a, cmd))
        scored.sort(key=lambda x: x[:4])
        return [{"label": a["name"], "cmd": cmd, "sub": "app"}
                for _t, _u, _s, _l, a, cmd in scored[:limit]]

    @Slot(str, result="QVariantMap")
    def calc(self, text):
        r = calc_text(text)
        if not r:
            return {}
        q = shlex.quote(r)
        return {"label": "= " + r,
                "cmd": f"wl-copy {q} 2>/dev/null || qdbus6 org.kde.klipper /klipper setClipboardContents {q}",
                "sub": "calc · click to copy"}

    @Slot(str, result="QVariantMap")
    def webItem(self, text):
        url = self._search_url.replace("%s", urllib.parse.quote_plus(text))
        return {"label": "Search: " + truncate(text, 18), "cmd": f"xdg-open {shlex.quote(url)}", "sub": "web"}

    # ---- dynamic rings -------------------------------------------------------
    def _recent_files(self, folder):
        try:
            files = [e for e in os.scandir(folder) if e.is_file() and not e.name.startswith(".")]
            files.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return [{"label": e.name, "cmd": f"xdg-open {shlex.quote(e.path)}"} for e in files[:MAX_DYNAMIC]]

    def _parse_dynamic(self, kind, out):
        items = []
        if kind == "wifi":
            saved_part, _, list_part = out.partition("@@@@")
            saved = set()
            for line in saved_part.splitlines():
                parts = split_nmcli(line)
                if len(parts) >= 2 and "wireless" in parts[1]:
                    saved.add(parts[0])
            best = {}
            for line in list_part.splitlines():
                parts = split_nmcli(line)
                if len(parts) < 4 or not parts[1] or parts[0] not in ("*", " ", ""):
                    continue
                inuse, ssid, secured = parts[0] == "*", parts[1], bool(parts[3].strip()) and parts[3].strip() != "--"
                try:
                    sig = int(parts[2])
                except ValueError:
                    sig = 0
                if ssid not in best or (inuse, sig) > best[ssid][:2]:
                    best[ssid] = (inuse, sig, secured)
            for ssid, (inuse, sig, secured) in sorted(best.items(), key=lambda kv: kv[1][:2], reverse=True):
                q = shlex.quote(ssid)
                item = {"label": ssid, "sub": "connected" if inuse else f"{sig}%"}
                if ssid in saved or not secured:
                    item["cmd"] = f"nmcli --wait 10 dev wifi connect {q}" if not ssid in saved else f"nmcli --wait 10 con up id {q}"
                else:
                    # new secured network: ask for the password in a terminal
                    item["cmd"] = f"nmcli --ask dev wifi connect {q}; echo; printf 'press enter to close '; read _"
                    item["term"] = True
                items.append(item)
            if not items:
                low = list_part.lower()
                if "disabled" in low or "not enabled" in low or "wi-fi is" in low:
                    items.append({"label": "Wi-Fi is off", "sub": "select to turn on", "cmd": "nmcli radio wifi on"})
                elif "not found" in low or "not found" in saved_part.lower():
                    items.append({"label": "nmcli missing", "sub": "install networkmanager", "cmd": "true"})
                else:
                    items.append({"label": "no networks yet", "sub": "select to rescan", "cmd": "nmcli dev wifi rescan"})
        elif kind == "clip":
            seen = set()
            for line in out.splitlines():
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    items.append({"label": line, "sub": "copy back",
                                  "cmd": "qdbus6 org.kde.klipper /klipper setClipboardContents " + shlex.quote(line)})
        else:
            try:
                data = json.loads(out)
                items = data if isinstance(data, list) else []
            except ValueError:
                for line in out.splitlines():
                    if "\t" in line:
                        label, cmd = line.split("\t", 1)
                        items.append({"label": label.strip(), "cmd": cmd.strip()})
        return items

    @Slot(str, str)
    def loadDynamic(self, key, spec):
        spec = (spec or "").strip()
        if spec.startswith("builtin:recent"):
            folder = spec.split(":", 2)[2] if spec.count(":") >= 2 else "~/Downloads"
            items = self._recent_files(os.path.expanduser(folder))
            self.dynamicReady.emit(key, json.dumps(clean_dynamic(items)))
            return
        if spec == "builtin:wifi":
            # cached scan list (no blocking rescan, which could outlive the 4s timeout),
            # saved profiles first, stderr merged so errors can be shown; rescan fires in the background
            cmd = ("nmcli -t -f NAME,TYPE con show 2>&1; echo '@@@@'; "
                   "nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan no 2>&1; "
                   "(nmcli dev wifi rescan >/dev/null 2>&1 &)")
            kind = "wifi"
        elif spec == "builtin:clipboard":
            cmd, kind = "qdbus6 org.kde.klipper /klipper getClipboardHistoryMenu", "clip"
        else:
            cmd, kind = spec, "lines"
        p = QProcess(self)
        p.finished.connect(lambda code, _s, p=p, key=key, kind=kind: self._dyn_done(p, key, kind))
        QTimer.singleShot(4000, lambda p=p: self._kill(p))
        p.start("/bin/sh", ["-c", cmd])

    @staticmethod
    def _kill(p):
        try:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        except RuntimeError:
            pass

    def _dyn_done(self, p, key, kind):
        out = bytes(p.readAllStandardOutput()).decode(errors="ignore")
        p.deleteLater()
        self.dynamicReady.emit(key, json.dumps(clean_dynamic(self._parse_dynamic(kind, out))))

    # ---- volume read-back, battery, cursor ------------------------------------
    @Property("QVariantMap", notify=volumeChanged)
    def volume(self):
        return self._volume

    @Slot()
    def pollVolume(self):
        if not shutil.which("wpctl") or self._vp is not None:
            return
        p = QProcess(self)
        self._vp = p
        p.finished.connect(self._vol_done)
        p.start("wpctl", ["get-volume", "@DEFAULT_AUDIO_SINK@"])

    def _vol_done(self, _code, _status):
        p, self._vp = self._vp, None
        if p is None:
            return
        out = bytes(p.readAllStandardOutput()).decode(errors="ignore")
        p.deleteLater()
        v = parse_volume(out)
        if v:
            self._volume = v
            self.volumeChanged.emit()

    @Slot(result=str)
    def battery(self):
        try:
            for d in sorted(Path("/sys/class/power_supply").glob("BAT*")):
                cap = (d / "capacity").read_text().strip()
                st = (d / "status").read_text().strip()
                return f"bat {cap}%" + (" charging" if st == "Charging" else "")
        except OSError:
            pass
        return ""

    @Slot(result="QVariantMap")
    def cursorInfo(self):
        pos = QCursor.pos()
        screens = QGuiApplication.screens()
        for i, s in enumerate(screens):
            g = s.geometry()
            if g.contains(pos):
                valid = not (pos.x() == 0 and pos.y() == 0)
                return {"screen": i, "x": pos.x() - g.x(), "y": pos.y() - g.y(), "valid": valid}
        return {"screen": 0, "x": 0, "y": 0, "valid": False}

    # ---- album art colour -------------------------------------------------------
    @Property(str, notify=artColorChanged)
    def artColor(self):
        return self._art_color

    def _set_art(self, img):
        color = dominant_color(img)
        if color != self._art_color:
            self._art_color = color
            self.artColorChanged.emit()

    def _handle_art(self, url):
        if not url:
            self._art_url = ""
            self._set_art(QImage())
            return
        if url == self._art_url:
            return
        self._art_url = url
        if url.startswith("file://"):
            self._set_art(QImage(QUrl(url).toLocalFile()))
        elif url.startswith(("http://", "https://")):
            if self._nam is None:
                self._nam = QNetworkAccessManager(self)
            reply = self._nam.get(QNetworkRequest(QUrl(url)))
            reply.finished.connect(lambda r=reply, u=url: self._art_done(r, u))

    def _art_done(self, reply, url):
        data = bytes(reply.readAll())
        reply.deleteLater()
        if url != self._art_url:
            return
        img = QImage()
        img.loadFromData(data)
        self._set_art(img)

    # ---- on/off state dots ---------------------------------------------------
    @Property("QVariantMap", notify=statesChanged)
    def states(self):
        return self._states

    @Slot("QVariantList")
    def pollStates(self, cmds):
        for cmd in cmds:
            if not cmd:
                continue
            p = QProcess(self)
            p.finished.connect(lambda code, _st, p=p, cmd=cmd: self._state_done(p, cmd, code))
            p.start("/bin/sh", ["-c", str(cmd)])

    def _state_done(self, p, cmd, code):
        p.deleteLater()
        if self._states.get(cmd) != (code == 0):
            self._states = {**self._states, cmd: code == 0}
            self.statesChanged.emit()

    @Property("QVariantMap", notify=trackChanged)
    def track(self):
        return self._track

    @Slot()
    def pollNowPlaying(self):
        if not shutil.which("playerctl"):
            self._track = {"status": "missing"}
            self.trackChanged.emit()
            return
        if self._pc is not None:
            return
        pc = QProcess(self)
        pc.finished.connect(self._poll_done)
        self._pc = pc
        fmt = "{{status}}\t{{artist}}\t{{title}}\t{{position}}\t{{mpris:length}}\t{{mpris:artUrl}}"
        pc.start("playerctl", ["metadata", "--format", fmt])

    def _poll_done(self, code, _status):
        pc, self._pc = self._pc, None
        if pc is None:
            return
        out = bytes(pc.readAllStandardOutput()).decode(errors="ignore").strip()
        pc.deleteLater()
        track = {}
        art = ""
        if code == 0 and out:
            parts = (out.split("\t") + [""] * 6)[:6]
            art = parts[5].strip()

            def secs(v):
                try:
                    return int(v) / 1_000_000
                except ValueError:
                    return 0.0

            track = {
                "status": parts[0],
                "artist": parts[1],
                "title": parts[2],
                "pos": secs(parts[3]),
                "length": secs(parts[4]),
            }
        self._handle_art(art)
        self._track = track
        self.trackChanged.emit()

    @Slot(float)
    def seek(self, secs):
        QProcess.startDetached("playerctl", ["position", str(max(0.0, secs))])

    @Slot(int)
    def adjustVolume(self, delta):
        if shutil.which("wpctl"):
            sign = "+" if delta > 0 else "-"
            QProcess.startDetached("wpctl", ["set-volume", "@DEFAULT_AUDIO_SINK@", f"3%{sign}"])
        elif shutil.which("pactl"):
            sign = "+" if delta > 0 else "-"
            QProcess.startDetached("pactl", ["set-sink-volume", "@DEFAULT_SINK@", f"{sign}3%"])
        QTimer.singleShot(90, self.pollVolume)

    @Property("QVariantList", notify=binsChanged)
    def bins(self):
        return self._bins

    @Slot(result=str)
    def loadMenu(self):
        err, data = "", None
        try:
            if not MENU_PATH.exists():
                SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
                MENU_PATH.write_text(json.dumps(DEFAULT_MENU, indent=2))
            data = json.loads(MENU_PATH.read_text())
            if isinstance(data, list):
                data = {"items": data}
            if not isinstance(data, dict):
                raise ValueError("top level must be an object")
        except json.JSONDecodeError as e:
            err = f"menu.json line {e.lineno}: {e.msg}"
        except Exception as e:
            err = f"menu.json: {e}"
        if data is None:
            data = json.loads(json.dumps(DEFAULT_MENU))
        data["items"] = clean_items(data.get("items"))
        if not data["items"]:
            data["items"] = clean_items(DEFAULT_MENU["items"])
            err = err or "menu.json has no usable items"
        data["error"] = err + ("  (showing default menu)" if err else "")
        self._terminal = str(data.get("terminal") or "foot")
        self._search_url = str(data.get("search_url") or DEFAULT_SEARCH_URL)
        data["usage"] = {c: round(self._use_score(c), 3) for c in self._usage}
        return json.dumps(data)

    @Slot(str, bool)
    def run(self, cmd, term=False):
        if not cmd:
            return
        self._record_use(cmd)
        if term:
            cmd = f"{self._terminal} -e sh -c {shlex.quote(cmd)}"
        QProcess.startDetached("/bin/sh", ["-c", cmd])

    @Slot()
    def startAudio(self):
        if self._cava is not None or not shutil.which("cava"):
            return
        cfg = Path(tempfile.gettempdir()) / "radial-cava.conf"
        cfg.write_text(CAVA_CONFIG)
        proc = QProcess(self)
        proc.readyReadStandardOutput.connect(self._read)
        proc.start("cava", ["-p", str(cfg)])
        self._cava = proc
        self._buf = ""

    @Slot()
    def stopAudio(self):
        if self._cava is not None:
            self._cava.readyReadStandardOutput.disconnect(self._read)
            self._cava.terminate()
            self._cava.waitForFinished(300)
            self._cava.deleteLater()
            self._cava = None
        self._bins = []
        self.binsChanged.emit()

    def _read(self):
        if self._cava is None:
            return
        self._buf += bytes(self._cava.readAllStandardOutput()).decode(errors="ignore")
        if "\n" not in self._buf:
            return
        *lines, self._buf = self._buf.split("\n")
        try:
            self._bins = [min(1.0, int(x) / 100) for x in lines[-1].split(";") if x.strip()]
        except ValueError:
            return
        self.binsChanged.emit()


QML = r'''
import QtQuick
import QtQuick.Window

Window {
    id: win
    visible: false
    color: "transparent"
    title: "radial"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

    property var menu: ({ items: [] })
    property string menuError: ""

    function openMenu() {
        try { menu = JSON.parse(backend.loadMenu()) }
        catch (e) { menu = { items: [], error: "could not read menu" } }
        menuError = menu.error || ""
        stage.artHex = backend.artColor
        stage.applyTheme(menu.theme || {})
        stage.iconsOn = menu.icons !== false
        stage.statusOn = menu.status !== false
        var ci = backend.cursorInfo()
        if (menu.screen !== "primary" && ci.valid) {
            try {
                var sc = Qt.application.screens[ci.screen]
                if (sc) win.screen = sc
            } catch (e) {}
        }
        stage.followCursor = (menu.position === "cursor") && ci.valid
        stage.curX = ci.x
        stage.curY = ci.y
        stage.reset()
        stage.blobK = 0.2
        visibility = Window.FullScreen
        raise()
        requestActivate()
        stage.forceActiveFocus()
        backend.startAudio()
        stage.pollStates()
        backend.pollNowPlaying()
        tick.last = Date.now()
        tick.start()
    }

    function closeMenu() {
        tick.stop()
        backend.stopAudio()
        visible = false
    }

    Connections {
        target: backend
        function onToggleRequested() {
            if (win.visible) win.closeMenu(); else win.openMenu()
        }
    }

    Connections {
        target: backend
        function onTrackChanged() {
            var t = backend.track
            stage.npStatus = t.status || ""
            stage.npArtist = t.artist || ""
            stage.npTitle = t.title || ""
            stage.npPos = t.pos || 0
            stage.npLen = t.length || 0
            stage.npStamp = Date.now()
        }
    }

    Connections {
        target: backend
        function onStatesChanged() {
            stage.stateMap = backend.states
        }
        function onArtColorChanged() {
            stage.artHex = backend.artColor
        }
        function onVolumeChanged() {
            var v = backend.volume
            stage.volPct = v.pct
            stage.volMuted = v.muted
            stage.volShow = 1.6
        }
        function onDynamicReady(key, js) {
            stage.applyDynamic(key, js)
        }
    }

    Timer {
        interval: 800
        repeat: true
        running: stage.showNp && win.visible
        triggeredOnStart: true
        onTriggered: backend.pollNowPlaying()
    }

    Timer {
        id: afterAction
        interval: 240
        onTriggered: { backend.pollNowPlaying(); stage.pollStates() }
    }

    Timer {
        id: afterSlow
        interval: 900
        onTriggered: stage.pollStates()
    }

    Timer {
        interval: 4000
        repeat: true
        running: win.visible && !stage.showNp
        onTriggered: backend.pollNowPlaying()
    }

    Timer {
        interval: 1000
        repeat: true
        running: win.visible && stage.statusOn
        triggeredOnStart: true
        onTriggered: {
            var d = new Date()
            var t = Qt.formatTime(d, "hh:mm") + "  ·  " + Qt.formatDate(d, "ddd d MMM")
            var b = backend.battery()
            stage.statusText = b ? t + "  ·  " + b : t
        }
    }

    Timer {
        id: tick
        interval: 16
        repeat: true
        property double last: 0
        onTriggered: {
            var now = Date.now()
            var dt = Math.min(0.045, (now - last) / 1000)
            last = now
            stage.step(dt)
        }
    }

    Item {
        id: stage
        anchors.fill: parent
        focus: true
        opacity: 0
        objectName: "stage"

        property var cSurface: [42, 48, 58]
        property var cHover:   [136, 192, 208]
        property var cCore:    [143, 188, 187]
        property var cDeep:    [94, 129, 172]
        property var cBase:    [46, 52, 64]
        property var cText:    [236, 239, 244]
        property var cInk:     [18, 20, 26]
        property var cBranch:  [163, 190, 140]
        property var cDim:     [14, 17, 22]
        property real dimAlpha: 0.8
        property string fontName: "Monocraft"
        property string iconFont: "Symbols Nerd Font"
        property bool iconsOn: true
        property var stateMap: ({})
        property var searchRes: []
        property var baseHover: [136, 192, 208]
        property bool albumOn: true
        property string artHex: ""
        property bool statusOn: true
        property string statusText: ""
        property real volPct: 0
        property bool volMuted: false
        property real volShow: 0
        property bool followCursor: false
        property real curX: 0
        property real curY: 0
        property int dynSeq: 0
        property bool dragging: false
        property bool consumed: false
        property bool openedByPress: false
        property bool pressOnBlob: false
        property var outItems: []
        property var outL: []
        property real outT: 0
        readonly property real centerX: followCursor ? Math.max(320, Math.min(width - 320, curX)) : width / 2
        readonly property real centerY: followCursor ? Math.max(320, Math.min(height - 320, curY)) : height / 2

        readonly property color accentC: Qt.rgba(cHover[0] / 255, cHover[1] / 255, cHover[2] / 255, 1)
        readonly property color coreC:   Qt.rgba(cCore[0] / 255, cCore[1] / 255, cCore[2] / 255, 1)
        readonly property color deepC:   Qt.rgba(cDeep[0] / 255, cDeep[1] / 255, cDeep[2] / 255, 1)
        readonly property color textC:   Qt.rgba(cText[0] / 255, cText[1] / 255, cText[2] / 255, 1)
        readonly property color branchC: Qt.rgba(cBranch[0] / 255, cBranch[1] / 255, cBranch[2] / 255, 1)
        readonly property color dimC:    Qt.rgba(cDim[0] / 255, cDim[1] / 255, cDim[2] / 255, 1)

        readonly property real r0: 50
        readonly property real r1: 126
        readonly property real r2: 244
        readonly property int  nPts: 88
        readonly property int  nParts: 42

        property bool expanded: false
        property var path: []
        property var angles: []
        property real ringT: 0
        property var hov: []
        property var hovV: []
        property var lv: []
        property var parts: []
        property int hoverIdx: -1
        property bool overBlob: false
        property real t: 0
        property real blobK: 0.78
        property real energy: 0
        property real energySmooth: 0
        property string crumb: ""
        property real punch: 0
        property real flash: 0
        property real breathe: 0

        property string filter: ""

        property bool showNp: false
        property string npStatus: ""
        property string npTitle: ""
        property string npArtist: ""
        property real npPos: 0
        property real npLen: 0
        property real npCur: 0
        property double npStamp: 0

        function reset() {
            expanded = false
            path = []
            angles = []
            ringT = 0
            hov = []
            hovV = []
            hoverIdx = -1
            overBlob = false
            blobK = 0.62
            energy = 0
            energySmooth = 0
            opacity = 0
            crumb = ""
            punch = 0
            flash = 0
            breathe = 0
            filter = ""
            searchRes = []
            showNp = false
            npStatus = ""; npTitle = ""; npArtist = ""
            npPos = 0; npLen = 0; npCur = 0
            lv = []
            for (var i = 0; i < nPts; i++) lv.push(0)
            parts = []
            for (var p = 0; p < nParts; p++) {
                parts.push({
                    a: Math.random() * Math.PI * 2,
                    r: 70 + Math.random() * 90,
                    life: Math.random(),
                    spd: 0.15 + Math.random() * 0.35,
                    sz: 0.6 + Math.random() * 1.4
                })
            }
        }

        function rawItems() {
            if (path.length) return path[path.length - 1].children || []
            var base = win.menu.items || []
            return base
        }

        function items() {
            return filter ? searchRes : rawItems()
        }

        function applyTheme(th) {
            function pick(v, d) { var c = hexToRgb(v); return c ? c : d }
            cHover   = pick(th.accent,  [136, 192, 208])
            cCore    = pick(th.accent2, [143, 188, 187])
            cDeep    = pick(th.deep,    [94, 129, 172])
            cBase    = pick(th.base,    [46, 52, 64])
            cSurface = pick(th.surface, [42, 48, 58])
            cText    = pick(th.text,    [236, 239, 244])
            cInk     = pick(th.ink,     [18, 20, 26])
            cBranch  = pick(th.branch,  [163, 190, 140])
            cDim     = pick(th.dim,     [14, 17, 22])
            dimAlpha = (typeof th.dimOpacity === "number")
                       ? Math.max(0, Math.min(1, th.dimOpacity)) : 0.8
            fontName = th.font || "Monocraft"
            iconFont = th.iconFont || "Symbols Nerd Font"
            baseHover = cHover.slice()
            albumOn = th.albumColors !== false
            var art = (albumOn && artHex) ? hexToRgb(artHex) : null
            if (art) cHover = art
        }

        function stateCmds(nodes, out) {
            for (var i = 0; i < nodes.length; i++) {
                if (nodes[i].state) out.push(nodes[i].state)
                if (nodes[i].children) stateCmds(nodes[i].children, out)
            }
        }

        function pollStates() {
            var out = []
            stateCmds(win.menu.items || [], out)
            if (out.length) backend.pollStates(out)
        }

        function collectDeep(nodes, trail, out) {
            for (var i = 0; i < nodes.length; i++) {
                var n = nodes[i]
                var nt = trail ? trail + " › " + n.label : String(n.label)
                if (n.children && n.children.length) collectDeep(n.children, nt, out)
                else if (n.cmd) out.push({ node: n, trail: trail })
            }
        }

        function fscore(q, text) {
            var t = String(text).toLowerCase()
            var i = t.indexOf(q)
            if (i === 0) return 0
            if (i > 0) return " -_.:/(".indexOf(t.charAt(i - 1)) >= 0 ? 1 : 2
            if (q.length < 2) return -1
            var pos = 0, first = -1, last = -1, n = 0
            for (var k = 0; k < q.length; k++) {
                var ch = q.charAt(k)
                if (ch === " ") continue
                var j = t.indexOf(ch, pos)
                if (j < 0) return -1
                if (first < 0) first = j
                last = j
                pos = j + 1
                n++
            }
            if (first < 0) return -1
            return 3 + Math.min(last - first + 1 - n, 40) / 50
        }

        function runSearch() {
            var q = filter.toLowerCase().replace(/^\s+|\s+$/g, "")
            if (!q) { searchRes = []; return }
            var res = [], seen = {}
            var cap = 8
            var use = win.menu.usage || {}

            function useOf(n) { return n.cmd ? (use[n.cmd] || 0) : 0 }
            function add(node, sub) {
                if (res.length >= cap) return
                var key = (node.children ? "b:" : "c:") + (node.children ? node.label : node.cmd)
                if (seen[key]) return
                seen[key] = true
                var copy = {}
                for (var k in node) copy[k] = node[k]
                if (sub) copy.sub = sub
                res.push(copy)
            }
            function byRank(a, b) {
                var ta = Math.floor(a.r), tb = Math.floor(b.r)
                if (ta !== tb) return ta - tb
                if (a.f !== b.f) return b.f - a.f
                return a.r - b.r
            }

            var cv = backend.calc(filter)
            if (cv && cv.label) add(cv, "")

            var raw = rawItems(), here = []
            for (var i = 0; i < raw.length; i++) {
                var r = fscore(q, raw[i].label)
                if (r >= 0) here.push({ r: r, f: useOf(raw[i]), node: raw[i] })
            }
            here.sort(byRank)
            for (var a = 0; a < here.length; a++) add(here[a].node, "")

            var deep = [], hits = []
            collectDeep(win.menu.items || [], "", deep)
            for (var d = 0; d < deep.length; d++) {
                var rd = fscore(q, deep[d].node.label)
                if (rd >= 0) hits.push({ r: rd, f: useOf(deep[d].node), node: deep[d].node, trail: deep[d].trail })
            }
            hits.sort(byRank)
            for (var h = 0; h < hits.length; h++) add(hits[h].node, hits[h].trail)

            if (res.length < cap) {
                var apps = backend.searchApps(filter.trim(), cap - res.length) || []
                for (var p = 0; p < apps.length; p++) add(apps[p], "app")
            }
            if (res.length === 0 && q.length >= 2) add(backend.webItem(filter.trim()), "web")
            searchRes = res
        }

        function hintText() {
            if (filter && items().length === 0) return "no matches"
            if (hoverIdx >= 0) {
                var it = items()
                if (hoverIdx < it.length) return it[hoverIdx].sub || it[hoverIdx].desc || ""
            }
            return ""
        }

        function layout() {
            var n = items().length, out = [], step, start
            if (filter) {
                step = Math.min(Math.PI * 0.34, 1.96 * Math.PI / Math.max(n, 1))
                start = -Math.PI / 2 - (n - 1) * step / 2
            } else if (!path.length || n >= 5) {
                // root ring, or any ring with 5+ items: even, full-circle, symmetric
                step = 2 * Math.PI / Math.max(n, 1)
                start = -Math.PI / 2
            } else {
                step = Math.min(Math.PI * 0.34, 1.96 * Math.PI / Math.max(n, 1))
                start = angles[angles.length - 1] - (n - 1) * step / 2
            }
            for (var i = 0; i < n; i++)
                out.push({ a: start + i * step, step: step })
            return out
        }

        function refreshCrumb() {
            var p = []
            for (var i = 0; i < path.length; i++) p.push(path[i].label)
            crumb = p.join("  ›  ")
            var n = path.length ? path[path.length - 1] : null
            showNp = !!n && (n.nowplaying === true ||
                     (n.nowplaying === undefined && String(n.label).toLowerCase() === "media"))
        }

        function pt(r, a) {
            return {
                x: canvas.width / 2 + r * Math.cos(a),
                y: canvas.height / 2 + r * Math.sin(a)
            }
        }

        function poly(a, step, e, off) {
            var vs = Math.min(step, Math.PI * 0.59)
            var k = Math.max(0, Math.min(e, 1.12))
            var rin = r0 * 0.7 + (r1 - r0 * 0.7) * k + off
            var rout = r0 * 0.85 + (r2 - r0 * 0.85) * k + off
            var aa = a + (1 - Math.min(e, 1)) * 0.38
            var hi = Math.max(0.02, vs / 2 - 7 / Math.max(rin, 1))
            var ho = Math.max(0.02, vs / 2 - 7 / Math.max(rout, 1))
            var P0 = pt(rin, aa - hi), P1 = pt(rout, aa - ho)
            var P2 = pt(rout, aa + ho), P3 = pt(rin, aa + hi)
            var c = 20 * e
            function lerp(A, B, d) {
                var dx = B.x - A.x, dy = B.y - A.y
                var len = Math.sqrt(dx * dx + dy * dy) || 1
                return { x: A.x + dx * d / len, y: A.y + dy * d / len }
            }
            var A1 = lerp(P1, P0, c), B1 = lerp(P1, P2, c)
            var B2 = lerp(P2, P1, c), A2 = lerp(P2, P3, c)
            return {
                aa: aa, rin: rin, rout: rout, vs: vs,
                p: [P0, A1, B1, B2, A2, P3],
                edge: [B1, B2]
            }
        }

        function inPoly(x, y, P) {
            var s = 0
            for (var i = 0; i < P.length; i++) {
                var A = P[i], B = P[(i + 1) % P.length]
                var c = (B.x - A.x) * (y - A.y) - (B.y - A.y) * (x - A.x)
                if (c !== 0) {
                    var sg = c > 0 ? 1 : -1
                    if (s === 0) s = sg
                    else if (sg !== s) return false
                }
            }
            return true
        }

        function hit(x, y) {
            if (!expanded) return -1
            var L = layout()
            for (var i = 0; i < L.length; i++) {
                if (ringT - i * 0.08 < 0.5) continue
                if (inPoly(x, y, poly(L[i].a, L[i].step, 1, 0).p)) return i
            }
            return -1
        }

        function hover(x, y) {
            var dx = x - canvas.width / 2, dy = y - canvas.height / 2
            overBlob = Math.sqrt(dx * dx + dy * dy) < r0 + 22
            hoverIdx = overBlob ? -1 : hit(x, y)
        }

        function click(x, y) {
            hover(x, y)
            if (overBlob) {
                punch = 1.25
                blobK = 1.42
                flash = 0.9
                if (path.length || filter) {
                    if (filter) { filter = ""; hoverIdx = -1; return }
                    back()
                } else {
                    if (expanded) startOut()
                    expanded = !expanded
                    punch = 0.9
                    ringT = 0
                    hov = []
                    hovV = []
                }
            } else if (hoverIdx >= 0) {
                pick(hoverIdx)
            } else {
                var dx = x - canvas.width / 2, dy = y - canvas.height / 2
                var far = Math.sqrt(dx * dx + dy * dy)
                if (far > (expanded ? r2 + 45 : r0 + 40)) win.closeMenu()
            }
        }

        function isBranch(n) {
            return !!((n.children && n.children.length) || n.dynamic)
        }

        function applyDynamic(key, js) {
            if (!path.length) return
            var nd = path[path.length - 1]
            if (String(nd._key) !== String(key)) return
            var kids = []
            try { kids = JSON.parse(js) } catch (e) {}
            nd.children = kids.length ? kids : [{ label: "nothing found", cmd: "" }]
            hov = []
            hovV = []
            ringT = 0
            hoverIdx = -1
        }

        function nearestWedge(a) {
            var L = layout(), best = -1, bd = 1e9
            for (var i = 0; i < L.length; i++) {
                var d = a - L[i].a
                while (d > Math.PI) d -= 2 * Math.PI
                while (d < -Math.PI) d += 2 * Math.PI
                d = Math.abs(d)
                if (d < bd) { bd = d; best = i }
            }
            if (best < 0) return -1
            return bd <= L[best].step * 0.75 ? best : -1
        }

        function pressAt(x, y) {
            hover(x, y)
            dragging = false
            consumed = false
            openedByPress = false
            pressOnBlob = overBlob
            if (overBlob && !expanded && !path.length && !filter) {
                expanded = true
                ringT = 0
                hov = []
                hovV = []
                openedByPress = true
                flash = 0.9
            }
        }

        function dragTo(x, y) {
            var dx = x - canvas.width / 2, dy = y - canvas.height / 2
            if (pressOnBlob && expanded && Math.sqrt(dx * dx + dy * dy) > 34) {
                dragging = true
                overBlob = false
                hoverIdx = nearestWedge(Math.atan2(dy, dx))
            } else {
                dragging = false
                hover(x, y)
            }
        }

        function releaseAt(x, y) {
            if (dragging && hoverIdx >= 0) {
                consumed = true
                pick(hoverIdx)
            } else if (openedByPress) {
                consumed = true
            }
            dragging = false
            pressOnBlob = false
        }

        function drawVolume(ctx) {
            if (volShow <= 0.01) return
            var cx = canvas.width / 2, cy = canvas.height / 2
            var a = Math.min(1, volShow / 0.4)
            var rr = 104
            var frac = Math.max(0, Math.min(1, volPct / 100))
            ctx.lineCap = "round"
            ctx.lineWidth = 5
            ctx.beginPath()
            ctx.arc(cx, cy, rr, 0, Math.PI * 2)
            ctx.strokeStyle = rgba(cText[0], cText[1], cText[2], 0.12 * a)
            ctx.stroke()
            if (!volMuted && frac > 0) {
                ctx.beginPath()
                ctx.arc(cx, cy, rr, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2)
                ctx.strokeStyle = volPct > 100 ? rgba(235, 203, 139, a) : ac(a)
                ctx.stroke()
            }
            ctx.lineCap = "butt"
            ctx.font = "19px " + fontName
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillStyle = rgba(cInk[0], cInk[1], cInk[2], 0.9 * a)
            ctx.fillText(volMuted ? "muted" : (Math.round(volPct) + "%"), cx, cy)
        }

        function rightClick() {
            if (filter) { filter = ""; hoverIdx = -1; return }
            if (!back()) win.closeMenu()
        }

        function pick(i) {
            var it = items()
            if (i < 0 || i >= it.length) return
            var node = it[i]
            punch = 0.9
            flash = 1.0
            filter = ""
            if (node.children && node.children.length) {
                startOut()
                angles.push(layout()[i].a)
                path.push(node)
                ringT = -0.55
                hov = []
                hovV = []
                hoverIdx = -1
                refreshCrumb()
            } else if (node.dynamic) {
                var nd = {}
                for (var dk in node) nd[dk] = node[dk]
                nd._key = ++dynSeq
                nd.children = [{ label: "loading…", cmd: "" }]
                startOut()
                angles.push(layout()[i].a)
                path.push(nd)
                ringT = -0.55
                hov = []
                hovV = []
                hoverIdx = -1
                refreshCrumb()
                backend.loadDynamic(String(nd._key), String(node.dynamic))
            } else if (node.cmd) {
                backend.run(node.cmd, !!node.term)
                var keep = node.keep !== undefined ? node.keep : showNp
                if (keep) { afterAction.restart(); afterSlow.restart() }
                else win.closeMenu()
            }
        }

        function back() {
            startOut()
            filter = ""
            if (path.length) {
                path.pop()
                angles.pop()
                ringT = -0.55
                hov = []
                hovV = []
                hoverIdx = -1
                refreshCrumb()
                return true
            }
            if (expanded) {
                expanded = false
                ringT = 0
                hov = []
                hovV = []
                return true
            }
            return false
        }

        function cycle(dir) {
            if (!expanded) return
            var n = items().length
            if (!n) return
            if (hoverIdx < 0 || hoverIdx >= n)
                hoverIdx = dir > 0 ? 0 : n - 1
            else
                hoverIdx = (hoverIdx + dir + n) % n
        }

        function appendFilter(ch) {
            if (!expanded) {
                expanded = true
                ringT = 0
                hov = []
                hovV = []
            }
            filter += ch
            runSearch()
            hoverIdx = items().length ? 0 : -1
            ringT = Math.max(ringT, 2)
        }

        function backspaceFilter() {
            if (filter.length) {
                filter = filter.slice(0, -1)
                if (filter) runSearch()
                hoverIdx = items().length ? 0 : -1
                return true
            }
            return false
        }

        Keys.onPressed: (e) => {
            if (filter && e.key === Qt.Key_Space) {
                appendFilter(" ")
                e.accepted = true
                return
            }
            if (e.key === Qt.Key_Tab) { cycle(1); e.accepted = true; return }
            if (e.key === Qt.Key_Backtab) { cycle(-1); e.accepted = true; return }

            if (e.text && e.text.length === 1 && e.text >= " " && e.key !== Qt.Key_Space) {
                if (/^[a-zA-Z0-9\-_.+*\/()^%]$/.test(e.text)) {
                    appendFilter(e.text.toLowerCase())
                    e.accepted = true
                    return
                }
            }

            if (e.key === Qt.Key_Escape) {
                if (filter) { filter = ""; hoverIdx = -1; e.accepted = true; return }
                if (!back()) win.closeMenu()
            } else if (e.key === Qt.Key_Backspace) {
                if (!backspaceFilter()) back()
            } else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) {
                if (!expanded) {
                    expanded = true
                    ringT = 0
                    hov = []
                    hovV = []
                } else if (hoverIdx >= 0) {
                    pick(hoverIdx)
                }
            } else if (e.key === Qt.Key_Left || e.key === Qt.Key_Up) {
                cycle(-1)
            } else if (e.key === Qt.Key_Right || e.key === Qt.Key_Down) {
                cycle(1)
            }
        }

        function npPosNow() {
            if (npStatus === "Playing") {
                var p = npPos + (Date.now() - npStamp) / 1000
                return npLen > 0 ? Math.min(npLen, p) : p
            }
            return npPos
        }

        function fmt(s) {
            s = Math.max(0, Math.floor(s))
            var m = Math.floor(s / 60), r = s % 60
            return m + ":" + (r < 10 ? "0" : "") + r
        }

        function hexToRgb(hex) {
            if (!hex || typeof hex !== "string") return null
            hex = hex.replace("#", "")
            if (hex.length === 3)
                hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2]
            if (hex.length !== 6) return null
            var n = parseInt(hex, 16)
            if (isNaN(n)) return null
            return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
        }

        function step(dt) {
            t += dt
            ringT = Math.min(ringT + dt / 0.19, 14)
            opacity += (1 - opacity) * Math.min(1, dt * 9.5)
            blobK += (1 - blobK) * Math.min(1, dt * 8)
            punch += (0 - punch) * Math.min(1, dt * 12)
            flash += (0 - flash) * Math.min(1, dt * 6.5)
            breathe = 0.5 + 0.5 * Math.sin(t * 0.85)
            volShow = Math.max(0, volShow - dt)
            outT = Math.max(0, outT - dt / 0.17)
            var acTgt = (albumOn && artHex) ? (hexToRgb(artHex) || baseHover) : baseHover
            var acD = Math.abs(acTgt[0] - cHover[0]) + Math.abs(acTgt[1] - cHover[1]) + Math.abs(acTgt[2] - cHover[2])
            if (acD > 2) {
                var acK = Math.min(1, dt * 3)
                cHover = [cHover[0] + (acTgt[0] - cHover[0]) * acK,
                          cHover[1] + (acTgt[1] - cHover[1]) * acK,
                          cHover[2] + (acTgt[2] - cHover[2]) * acK]
            } else if (acD > 0) {
                cHover = acTgt.slice()
            }
            if (showNp) npCur = npPosNow()

            var n = items().length
            for (var i = 0; i < n; i++) {
                var cur = hov[i] || 0
                var vel = hovV[i] || 0
                var tgt = (i === hoverIdx && expanded) ? 1 : 0
                var force = (tgt - cur) * 210
                vel += force * dt
                vel *= Math.exp(-dt * 15.2)
                cur += vel * dt
                if (cur < 0) { cur = 0; vel *= 0.3 }
                if (cur > 1) { cur = 1; vel *= 0.3 }
                hov[i] = cur
                hovV[i] = vel
            }

            var bins = backend.bins
            var nb = bins.length
            var sum = 0
            for (var j = 0; j < nPts; j++) {
                var u = Math.abs(2 * (j / nPts) - 1)
                var tg
                if (nb > 1) {
                    var pos = u * (nb - 1)
                    var lo = Math.floor(pos)
                    var fr = pos - lo
                    tg = bins[lo] * (1 - fr) + bins[Math.min(lo + 1, nb - 1)] * fr
                    tg = Math.max(tg, 0.02 + 0.028 * Math.sin(t * 2.0 + j * 0.36))
                } else {
                    tg = Math.max(0,
                        0.11 + 0.095 * Math.sin(t * 2.3 + u * 6.0) * Math.sin(t * 0.98 + u * 2.6)
                        + 0.04 * Math.sin(t * 4.0 + u * 10)
                        + 0.022 * Math.sin(t * 0.65 + u * 1.3))
                }
                var k = tg > lv[j] ? 0.6 : 0.14
                lv[j] += (tg - lv[j]) * k
                sum += lv[j]
            }
            var targetEnergy = Math.min(1, sum / nPts * 1.9)
            energy += (targetEnergy - energy) * Math.min(1, dt * 5.5)
            energySmooth += (energy - energySmooth) * Math.min(1, dt * 2.2)

            for (var p = 0; p < parts.length; p++) {
                var pa = parts[p]
                pa.a += dt * pa.spd * (0.4 + energy * 1.2)
                pa.life += dt * (0.18 + energy * 0.35)
                if (pa.life > 1) {
                    pa.life = 0
                    pa.r = 65 + Math.random() * 110
                    pa.a = Math.random() * Math.PI * 2
                    pa.spd = 0.12 + Math.random() * 0.4
                    pa.sz = 0.5 + Math.random() * 1.6
                }
            }

            canvas.requestPaint()
        }

        function mix(a, b, k, alpha) {
            return "rgba(" +
                Math.round(a[0] + (b[0] - a[0]) * k) + "," +
                Math.round(a[1] + (b[1] - a[1]) * k) + "," +
                Math.round(a[2] + (b[2] - a[2]) * k) + "," +
                alpha + ")"
        }

        function rgba(r, g, b, a) {
            return "rgba(" + Math.round(r) + "," + Math.round(g) + "," + Math.round(b) + "," + a + ")"
        }

        function ac(a) { return rgba(cHover[0], cHover[1], cHover[2], a) }
        function cc(a) { return rgba(cCore[0], cCore[1], cCore[2], a) }

        function drawParticles(ctx) {
            var cx = canvas.width / 2, cy = canvas.height / 2
            for (var i = 0; i < parts.length; i++) {
                var p = parts[i]
                var life = p.life
                var alpha = life < 0.2 ? life / 0.2 : (life > 0.75 ? (1 - life) / 0.25 : 1)
                alpha *= 0.18 + energy * 0.35
                if (alpha < 0.01) continue
                var rr = p.r * (0.85 + energy * 0.25) * (0.9 + breathe * 0.08)
                var x = cx + rr * Math.cos(p.a)
                var y = cy + rr * Math.sin(p.a)
                var s = p.sz * (0.7 + energy * 0.8)
                ctx.beginPath()
                ctx.arc(x, y, s, 0, Math.PI * 2)
                ctx.fillStyle = ac(alpha)
                ctx.fill()
            }
        }

        function drawBlob(ctx) {
            var cx = canvas.width / 2, cy = canvas.height / 2
            var scale = blobK * (1 + punch * 0.22)

            function shape(extra, scaleMul) {
                var P = []
                var s = scale * (scaleMul || 1)
                for (var i = 0; i < nPts; i++) {
                    var th = i / nPts * 2 * Math.PI
                    var wave = 2.3 * Math.sin(t * 1.28 + th * 3.0)
                             + 1.15 * Math.sin(t * 1.95 + th * 5.1)
                             + 0.5 * Math.sin(t * 3.3 + th * 7.4)
                    var r = r0 * s + lv[i] * (36 + energy * 26) + wave + extra
                    P.push({
                        x: cx + r * Math.cos(th),
                        y: cy + r * Math.sin(th)
                    })
                }
                ctx.beginPath()
                ctx.moveTo((P[nPts - 1].x + P[0].x) / 2, (P[nPts - 1].y + P[0].y) / 2)
                for (var j = 0; j < nPts; j++) {
                    var q = P[(j + 1) % nPts]
                    ctx.quadraticCurveTo(P[j].x, P[j].y, (P[j].x + q.x) / 2, (P[j].y + q.y) / 2)
                }
                ctx.closePath()
            }

            shape(52, 1.09)
            ctx.fillStyle = ac(0.028 + energy * 0.045)
            ctx.fill()
            shape(36, 1.06)
            ctx.fillStyle = ac(0.05 + energy * 0.055)
            ctx.fill()
            shape(22, 1.03)
            ctx.fillStyle = cc(0.09 + energy * 0.07)
            ctx.fill()
            shape(11)
            ctx.fillStyle = ac(0.15 + energy * 0.05)
            ctx.fill()

            shape(0)
            var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0 * scale + 52)
            var cool = 0.45 + energySmooth * 0.5
            g.addColorStop(0.0, mix(cBranch, cHover, cool, 1))
            g.addColorStop(0.18, mix(cCore, cHover, cool * 0.7, 1))
            g.addColorStop(0.42, ac(1))
            g.addColorStop(0.72, rgba(cDeep[0], cDeep[1], cDeep[2], 1))
            g.addColorStop(1.0, rgba(cBase[0], cBase[1], cBase[2], 1))
            ctx.fillStyle = g
            ctx.fill()

            ctx.beginPath()
            ctx.arc(cx - 10, cy - 13, r0 * 0.34 * scale, 0, Math.PI * 2)
            ctx.fillStyle = rgba(236, 239, 244, 0.14 + energy * 0.12)
            ctx.fill()

            if (flash > 0.015) {
                ctx.beginPath()
                ctx.arc(cx, cy, r0 * scale + 26 + (1 - flash) * 55, 0, Math.PI * 2)
                ctx.lineWidth = 2.2 + flash * 1.5
                ctx.strokeStyle = ac(flash * 0.6)
                ctx.stroke()
            }
            if (energy > 0.08) {
                ctx.beginPath()
                ctx.arc(cx, cy, r0 * scale + 18 + energy * 12, 0, Math.PI * 2)
                ctx.lineWidth = 1.5
                ctx.strokeStyle = cc(energy * 0.35)
                ctx.stroke()
            }
        }

        function pathOf(ctx, P) {
            ctx.beginPath()
            ctx.moveTo(P[0].x, P[0].y)
            for (var k = 1; k < P.length; k++) ctx.lineTo(P[k].x, P[k].y)
            ctx.closePath()
        }

        function easeOutBack(x) {
            var d = x - 1
            return 1 + 2.2 * d * d * d + 1.2 * d * d
        }

        function drawRing(ctx) {
            drawRingOf(ctx, layout(), items(), ringT, false)
        }

        function drawRingOut(ctx) {
            drawRingOf(ctx, outL, outItems, outT * 1.6, true)
        }

        function startOut() {
            if (!expanded || ringT < 0.4) return
            outItems = items()
            outL = layout()
            outT = 1
        }

        function drawRingOf(ctx, L, it, T, isOut) {
            var te = Math.max(0, Math.min(1, T / 1.4))
            var tsc = 0.5 + 0.5 * (1 - Math.pow(1 - te, 3))

            ctx.beginPath()
            for (var q = 0; q < 120; q++) {
                var ta = q * Math.PI / 60
                var major = (q % 10 === 0)
                var mid = (q % 5 === 0)
                var len = major ? 11 : (mid ? 6.5 : 3.8)
                var t1 = pt((r2 + 30) * tsc, ta)
                var t2 = pt((r2 + 30 + len) * tsc, ta)
                ctx.moveTo(t1.x, t1.y)
                ctx.lineTo(t2.x, t2.y)
            }
            ctx.lineWidth = 1
            ctx.strokeStyle = rgba(216, 222, 233, 0.14 * te)
            ctx.stroke()

            for (var i = 0; i < L.length; i++) {
                var e = Math.max(0, Math.min(1, T - i * 0.08))
                if (e <= 0) continue
                e = isOut ? e * e * (3 - 2 * e) : easeOutBack(e)

                var h = isOut ? 0 : (hov[i] || 0)
                var lift = h * 17
                var g = poly(L[i].a, L[i].step, e, lift)
                var P = g.p

                var custom = hexToRgb(it[i].color)
                var hoverCol = custom || cHover

                if (h > 0.015) {
                    pathOf(ctx, poly(L[i].a, L[i].step, e, lift + 14 * h).p)
                    ctx.lineWidth = 3.5
                    ctx.strokeStyle = rgba(hoverCol[0], hoverCol[1], hoverCol[2], 0.25 * h * e)
                    ctx.stroke()
                }

                pathOf(ctx, poly(L[i].a, L[i].step, e, lift - 3).p)
                ctx.fillStyle = rgba(0, 0, 0, 0.22 * e)
                ctx.fill()

                pathOf(ctx, P)
                var fillK = 0.08 + h * 0.92
                ctx.fillStyle = mix(cSurface, hoverCol, fillK, (0.86 + h * 0.12) * e)
                ctx.fill()

                ctx.lineWidth = 1.4
                ctx.strokeStyle = mix([95, 108, 128], [190, 230, 240], h, e * 0.65)
                ctx.stroke()

                ctx.beginPath()
                ctx.moveTo(g.edge[0].x, g.edge[0].y)
                ctx.lineTo(g.edge[1].x, g.edge[1].y)
                ctx.lineWidth = 2.5 + h * 3.5
                ctx.strokeStyle = rgba(hoverCol[0], hoverCol[1], hoverCol[2], (0.48 + h * 0.52) * e)
                ctx.stroke()

                // labels, icons and markers fade in once the wedge has mostly grown
                e = Math.max(0, Math.min(1, (e - 0.55) / 0.45))

                var branch = isBranch(it[i])

                if (branch) {
                    var tip = pt(g.rout - 13, g.aa)
                    var bl  = pt(g.rout - 26, g.aa - 8 / Math.max(g.rout - 26, 1))
                    var br  = pt(g.rout - 26, g.aa + 8 / Math.max(g.rout - 26, 1))
                    ctx.beginPath()
                    ctx.moveTo(tip.x, tip.y)
                    ctx.lineTo(bl.x, bl.y)
                    ctx.lineTo(br.x, br.y)
                    ctx.closePath()
                    ctx.fillStyle = mix(cBranch, cInk, h * 0.6, e)
                    ctx.fill()
                }

                var sv = it[i].state ? stateMap[it[i].state] : undefined
                if (sv !== undefined) {
                    var dp = pt(g.rout - 17, g.aa)
                    ctx.beginPath()
                    ctx.arc(dp.x, dp.y, 4.6, 0, Math.PI * 2)
                    ctx.fillStyle = sv ? mix(cBranch, cInk, h * 0.6, e)
                                       : mix([88, 98, 118], cInk, h * 0.5, e * 0.9)
                    ctx.fill()
                    if (!sv) {
                        ctx.lineWidth = 1.2
                        ctx.strokeStyle = mix([120, 132, 154], cInk, h * 0.5, e * 0.9)
                        ctx.stroke()
                    }
                }

                var rc = (g.rin + g.rout) / 2 - (branch ? 12 : 0)
                var c = pt(rc, g.aa)
                var chord = 2 * rc * Math.sin(g.vs / 2) * 0.78
                var avail = Math.abs(Math.cos(g.aa)) * (g.rout - g.rin - (branch ? 40 : 26))
                          + Math.abs(Math.sin(g.aa)) * chord

                var txt = String(it[i].label)
                var size = 15
                ctx.font = size + "px " + fontName
                var w = ctx.measureText(txt).width
                var lines = [txt]

                if (w > avail && txt.indexOf(" ") >= 0) {
                    var mid = txt.length / 2, sp = -1, bd = 1e9
                    for (var ci = 0; ci < txt.length; ci++) {
                        if (txt.charAt(ci) === " " && Math.abs(ci - mid) < bd) {
                            bd = Math.abs(ci - mid)
                            sp = ci
                        }
                    }
                    if (sp > 0) {
                        lines = [txt.substring(0, sp), txt.substring(sp + 1)]
                        w = Math.max(ctx.measureText(lines[0]).width,
                                     ctx.measureText(lines[1]).width)
                    }
                }
                if (w > avail) {
                    size = Math.max(10, Math.floor(size * avail / w))
                    ctx.font = size + "px " + fontName
                }

                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                var lh = size * 1.2

                var glyph = (iconsOn && it[i].icon) ? String(it[i].icon) : ""
                var shift = glyph ? 9 : 0
                if (glyph) {
                    ctx.font = "21px '" + iconFont + "', " + fontName
                    ctx.fillStyle = mix(cHover, cInk, h * 0.78, e * (0.8 + h * 0.2))
                    ctx.fillText(glyph, c.x, c.y - 13 - (lines.length - 1) * lh / 2)
                    ctx.font = size + "px " + fontName
                }

                for (var li = 0; li < lines.length; li++) {
                    var ly = c.y + shift - (lines.length - 1) * lh / 2 + li * lh
                    ctx.fillStyle = rgba(0, 0, 0, 0.42 * e * (1 - h * 0.5))
                    ctx.fillText(lines[li], c.x, ly + 1.1)
                    ctx.fillStyle = mix(cText, cInk, h * 0.78, e)
                    ctx.fillText(lines[li], c.x, ly)
                }
            }

            if (hoverIdx >= 0 && hoverIdx < L.length) {
                var hh = hov[hoverIdx] || 0
                var off = hh * 17
                var s = pt(r0 * blobK + 42, L[hoverIdx].a)
                var f = pt(r1 + off, L[hoverIdx].a)
                ctx.beginPath()
                ctx.moveTo(s.x, s.y)
                ctx.lineTo(f.x, f.y)
                ctx.lineWidth = 1.6
                ctx.strokeStyle = ac(0.55 + hh * 0.35)
                ctx.stroke()
                ctx.beginPath()
                ctx.arc(f.x, f.y, 3.2 + hh * 1.6, 0, Math.PI * 2)
                ctx.fillStyle = ac(1)
                ctx.fill()
            }
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onClicked: (m) => {
                if (m.button === Qt.RightButton) stage.rightClick()
                else win.closeMenu()
            }
            onWheel: (w) => {
                if (stage.overBlob || !stage.expanded) {
                    backend.adjustVolume(w.angleDelta.y > 0 ? 1 : -1)
                } else if (stage.expanded) {
                    stage.cycle(w.angleDelta.y > 0 ? -1 : 1)
                }
            }
        }

        Rectangle {
            anchors.fill: parent
            color: stage.dimC
            opacity: stage.dimAlpha
        }

        Canvas {
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d")
                var g = ctx.createRadialGradient(
                    width/2, height/2, Math.min(width, height)*0.12,
                    width/2, height/2, Math.max(width, height)*0.78
                )
                g.addColorStop(0, "rgba(0,0,0,0)")
                g.addColorStop(0.45, "rgba(0,0,0,0.28)")
                g.addColorStop(1, "rgba(0,0,0,0.7)")
                ctx.fillStyle = g
                ctx.fillRect(0, 0, width, height)
            }
            Component.onCompleted: requestPaint()
        }

        Canvas {
            id: canvas
            width: 600
            height: 600
            x: stage.centerX - width / 2
            y: stage.centerY - height / 2
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                stage.drawParticles(ctx)
                stage.drawBlob(ctx)
                stage.drawVolume(ctx)
                if (stage.outT > 0) stage.drawRingOut(ctx)
                if (stage.expanded) stage.drawRing(ctx)
            }
        }

        MouseArea {
            anchors.fill: canvas
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: (stage.overBlob || stage.hoverIdx >= 0)
                         ? Qt.PointingHandCursor : Qt.ArrowCursor
            onPressed: (m) => { if (m.button === Qt.LeftButton) stage.pressAt(m.x, m.y) }
            onReleased: (m) => { if (m.button === Qt.LeftButton) stage.releaseAt(m.x, m.y) }
            onPositionChanged: (m) => {
                if (pressed) stage.dragTo(m.x, m.y)
                else stage.hover(m.x, m.y)
            }
            onClicked: (m) => {
                if (m.button === Qt.RightButton) { stage.rightClick(); return }
                if (stage.consumed) { stage.consumed = false; return }
                stage.click(m.x, m.y)
            }
            onWheel: (w) => {
                if (stage.overBlob) {
                    backend.adjustVolume(w.angleDelta.y > 0 ? 1 : -1)
                } else if (stage.expanded) {
                    stage.cycle(w.angleDelta.y > 0 ? -1 : 1)
                }
            }
        }

        Text {
            anchors.horizontalCenter: canvas.horizontalCenter
            anchors.bottom: canvas.top
            anchors.bottomMargin: 10
            text: stage.filter ? ("search: " + stage.filter + " ▌") : ""
            color: stage.accentC
            opacity: stage.filter ? 0.95 : 0
            font.pixelSize: 16
            font.family: stage.fontName
            Behavior on opacity { NumberAnimation { duration: 120 } }
        }

        Text {
            anchors.horizontalCenter: canvas.horizontalCenter
            anchors.bottom: canvas.top
            anchors.bottomMargin: 36
            text: stage.hintText()
            color: stage.coreC
            opacity: text ? 0.9 : 0
            font.pixelSize: 14
            font.family: stage.fontName
            Behavior on opacity { NumberAnimation { duration: 120 } }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 26
            text: stage.statusOn ? stage.statusText : ""
            color: stage.textC
            opacity: 0.5
            font.pixelSize: 15
            font.family: stage.fontName
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            text: win.menuError ? ("⚠ " + win.menuError) : ""
            color: "#EBCB8B"
            opacity: text ? 0.95 : 0
            font.pixelSize: 15
            font.family: stage.fontName
        }

        Text {
            anchors.horizontalCenter: canvas.horizontalCenter
            anchors.top: canvas.bottom
            anchors.topMargin: 14
            text: stage.crumb
            color: stage.textC
            opacity: stage.crumb ? 0.88 : 0
            font.pixelSize: 15
            font.family: stage.fontName
            Behavior on opacity { NumberAnimation { duration: 150 } }
        }

        Rectangle {
            id: npCard
            visible: stage.showNp
            width: 420
            height: 80
            anchors.horizontalCenter: canvas.horizontalCenter
            anchors.top: canvas.bottom
            anchors.topMargin: 42
            radius: 9
            color: Qt.rgba(0.12, 0.14, 0.17, 0.97)
            border.width: 1
            border.color: "#353b48"

            Rectangle {
                width: 3
                height: parent.height
                radius: 1.5
                color: stage.npStatus === "Playing" ? stage.accentC : stage.deepC
                anchors.left: parent.left
            }

            Rectangle {
                width: 8; height: 8; radius: 4
                x: 16; y: 16
                color: stage.npStatus === "Playing" ? stage.branchC
                     : stage.npStatus === "Paused"  ? "#EBCB8B"
                     : "#4C566A"
            }

            Text {
                x: 32; y: 11
                width: parent.width - 48
                elide: Text.ElideRight
                color: stage.textC
                font.pixelSize: 15
                font.family: stage.fontName
                text: stage.npStatus === "missing" ? "playerctl is not installed"
                    : stage.npStatus === "" ? "Nothing playing"
                    : (stage.npTitle || "Unknown title")
            }

            Text {
                x: 16; y: 34
                width: parent.width - 32
                elide: Text.ElideRight
                color: stage.coreC
                font.pixelSize: 12
                font.family: stage.fontName
                text: stage.npArtist
                      + (stage.npStatus === "Paused"
                         ? (stage.npArtist ? "  ·  paused" : "Paused") : "")
            }

            Rectangle {
                id: track
                x: 16; y: 58
                width: parent.width - 32 - 100
                height: 5
                radius: 2.5
                color: "#2A303C"

                Rectangle {
                    id: fill
                    height: parent.height
                    width: stage.npLen > 0
                           ? parent.width * Math.min(1, stage.npCur / stage.npLen) : 0
                    radius: 2.5
                    color: stage.accentC
                }

                Rectangle {
                    visible: stage.npLen > 0
                    x: fill.width - 1.5
                    y: -3.5
                    width: 3
                    height: 12
                    radius: 1.5
                    color: stage.textC
                }
            }

            Text {
                x: track.x + track.width + 10
                y: 52
                color: "#D8DEE9"
                opacity: 0.82
                font.pixelSize: 12
                font.family: stage.fontName
                text: stage.fmt(stage.npCur) + " / " + stage.fmt(stage.npLen)
            }

            MouseArea {
                x: track.x; y: track.y - 10
                width: track.width; height: 22
                enabled: stage.npLen > 0
                cursorShape: Qt.PointingHandCursor
                onClicked: (m) => {
                    var s = m.x / width * stage.npLen
                    backend.seek(s)
                    stage.npPos = s
                    stage.npStamp = Date.now()
                    afterAction.restart()
                }
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {}
                z: -1
            }
        }
    }
}
'''


def main():
    daemon = "--daemon" in sys.argv
    quitting = "--quit" in sys.argv

    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if sock.waitForConnected(150):
        if not daemon:
            sock.write(b"quit" if quitting else b"toggle")
            sock.flush()
            sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return 0
    if quitting:
        return 0

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QGuiApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    backend = Backend()

    server = QLocalServer()
    QLocalServer.removeServer(SOCKET_NAME)
    server.listen(SOCKET_NAME)

    def on_connection():
        conn = server.nextPendingConnection()
        def on_ready(c=conn):
            msg = bytes(c.readAll()).decode(errors="ignore").strip()
            if msg == "quit":
                app.quit()
            else:
                backend.toggleRequested.emit()

        conn.readyRead.connect(on_ready)
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(QML.encode("utf-8"), QUrl("file:///radial.qml"))
    if not engine.rootObjects():
        return 1

    if not daemon:
        QTimer.singleShot(0, backend.toggleRequested.emit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
