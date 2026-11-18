# -*- coding: utf-8 -*-
"""Javad PC Doctor - portable Windows hardware check utility.
Safe, non-destructive checks. Some sensor/SMART readings depend on Windows and hardware support.
"""
import os, sys, json, time, platform, socket, threading, subprocess, datetime, tempfile, hashlib, re
from urllib.request import urlopen
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import psutil
except Exception:
    psutil = None

APP_NAME = 'Javad PC Doctor'
VERSION = '1.6.0'
BASE = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
ASSET_BASE = Path(getattr(sys, '_MEIPASS', BASE))
REPORTS = BASE / 'Reports'
REPORTS.mkdir(exist_ok=True)

BG = '#071629'; PANEL = '#0d2542'; PANEL2 = '#12365d'; CYAN = '#20d5ef'; TEXT = '#eaf5ff'; MUTED = '#a8c3dc'; GREEN = '#31d477'; YELLOW = '#ffc857'; RED = '#ff5c69'


def run_powershell(command, timeout=12):
    try:
        p = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                           capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace',
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return (p.stdout or '').strip()
    except Exception:
        return ''


def get_system_info():
    info = {'Computer': socket.gethostname(), 'OS': platform.platform(), 'Architecture': platform.machine(),
            'Processor': platform.processor() or 'نامشخص', 'Logical CPUs': os.cpu_count() or 'نامشخص'}
    if psutil:
        vm = psutil.virtual_memory()
        info['RAM total'] = f'{vm.total / (1024**3):.1f} GB'
        info['RAM available'] = f'{vm.available / (1024**3):.1f} GB'
        info['Boot time'] = datetime.datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M')
    try:
        gpu = run_powershell("Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name")
        info['GPU'] = gpu.replace('\n', ' | ') if gpu else 'قابل شناسایی نبود'
        disks = run_powershell("Get-CimInstance Win32_DiskDrive | ForEach-Object { $_.Model + ' (' + [math]::Round($_.Size/1GB) + ' GB)' }")
        info['Drives'] = disks.replace('\n', ' | ') if disks else 'قابل شناسایی نبود'
        board = run_powershell("(Get-CimInstance Win32_BaseBoard | Select-Object -First 1 -ExpandProperty Product)")
        info['Motherboard'] = board or 'نامشخص'
    except Exception: pass
    return info


def disk_health():
    # Windows reports a coarse health status; not a full SMART surface scan.
    out = run_powershell("Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus,MediaType | ConvertTo-Json -Compress")
    if not out: return 'وضعیت SMART/سلامت دیسک از ویندوز قابل دریافت نبود؛ ابزار تخصصی سازنده توصیه می‌شود.'
    try:
        data = json.loads(out)
        if isinstance(data, dict): data = [data]
        return '\n'.join(f"{d.get('FriendlyName','Disk')}: سلامت={d.get('HealthStatus','نامشخص')}، وضعیت={d.get('OperationalStatus','نامشخص')}، نوع={d.get('MediaType','نامشخص')}" for d in data)
    except Exception: return out



def _temperature_number(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r'-?\d+(?:[.,]\d+)?', str(value))
        return float(match.group(0).replace(',', '.')) if match else None
    except Exception:
        return None


def _collect_lhm_temperatures(node, path=None, found=None):
    """Read temperature nodes from LibreHardwareMonitor's data.json tree."""
    if path is None: path = []
    if found is None: found = []
    if isinstance(node, dict):
        label = str(node.get('Text') or node.get('Name') or '')
        current_path = path + ([label] if label else [])
        raw = node.get('Value')
        if raw is not None:
            value = _temperature_number(raw)
            raw_text = str(raw).lower()
            if value is not None and ('°c' in raw_text or ' c' in raw_text or
                                      str(node.get('Type', '')).lower() == 'temperature'):
                found.append((' / '.join(current_path), value))
        children = node.get('Children') or []
        if isinstance(children, list):
            for child in children:
                _collect_lhm_temperatures(child, current_path, found)
    elif isinstance(node, list):
        for child in node:
            _collect_lhm_temperatures(child, path, found)
    return found


def _classify_temperature_sensors(items):
    result = {'CPU': [], 'GPU': [], 'DISK': [], 'BOARD': []}
    for label, value in items:
        low = label.lower()
        if any(k in low for k in ('cpu', 'processor', 'package', 'core ', 'tctl', 'tdie', 'ccd')):
            bucket = 'CPU'
        elif any(k in low for k in ('gpu', 'nvidia', 'radeon', 'graphics', 'video')):
            bucket = 'GPU'
        elif any(k in low for k in ('nvme', 'ssd', 'hdd', 'disk', 'drive', 'storage')):
            bucket = 'DISK'
        elif any(k in low for k in ('motherboard', 'mainboard', 'system', 'chipset', 'pch')):
            bucket = 'BOARD'
        else:
            continue
        result[bucket].append((label, value))
    return result


def read_hardware_temperatures():
    """Prefer LibreHardwareMonitor (REST/WMI); use psutil only as a fallback."""
    raw_items = []
    source = ''
    # LibreHardwareMonitor 0.9.5+ exposes the sensor tree through its local web server.
    try:
        with urlopen('http://127.0.0.1:8085/data.json', timeout=1.2) as response:
            tree = json.loads(response.read().decode('utf-8', errors='replace'))
        raw_items = _collect_lhm_temperatures(tree)
        if raw_items:
            source = 'LibreHardwareMonitor'
    except Exception:
        pass

    # Older LibreHardwareMonitor/OpenHardwareMonitor versions publish WMI sensors.
    if not raw_items and os.name == 'nt':
        command = (
            "$ErrorActionPreference='Stop'; "
            "Get-CimInstance -Namespace 'root/LibreHardwareMonitor' -ClassName Sensor | "
            "Where-Object { $_.SensorType -eq 'Temperature' } | "
            "Select-Object Name,Identifier,Value | ConvertTo-Json -Compress"
        )
        try:
            output = run_powershell(command, timeout=5)
            data = json.loads(output) if output else []
            if isinstance(data, dict): data = [data]
            for sensor in data:
                value = _temperature_number(sensor.get('Value'))
                if value is not None:
                    raw_items.append((str(sensor.get('Identifier') or sensor.get('Name') or 'Sensor'), value))
            if raw_items: source = 'LibreHardwareMonitor WMI'
        except Exception:
            pass

    if not raw_items and psutil:
        try:
            for group, entries in psutil.sensors_temperatures().items():
                for entry in entries:
                    if entry.current is not None:
                        raw_items.append((str(entry.label or group), float(entry.current)))
            if raw_items: source = 'سیستم‌عامل / psutil'
        except Exception:
            pass

    result = _classify_temperature_sensors(raw_items)
    result['SOURCE'] = source
    result['AVAILABLE'] = bool(raw_items)
    return result


class DoctorApp:
    def __init__(self, root):
        self.root = root; self.root.title(f'{APP_NAME}  |  نسخه {VERSION}'); self.root.geometry('1280x820'); self.root.minsize(760, 560); self.root.configure(bg=BG)
        try: self.root.state('zoomed')
        except Exception: pass
        try:
            ico = ASSET_BASE / 'assets' / 'JavadPCDoctor.ico'
            if ico.exists(): self.root.iconbitmap(str(ico))
        except Exception: pass
        self.results = []; self.running = False; self.stress_running = False; self.stress_cancel = threading.Event(); self.info = get_system_info(); self.technician_assessment = 'انتخاب نشده'
        self._style(); self._build(); self.refresh_info(); self.temp_monitor_stop = threading.Event(); self.root.after(700, self.start_temperature_monitor)

    def _style(self):
        s = ttk.Style(); s.theme_use('clam')
        s.configure('TProgressbar', troughcolor='#183653', background=CYAN, bordercolor='#183653', lightcolor=CYAN, darkcolor=CYAN)
        s.configure('Treeview', background=PANEL, foreground=TEXT, fieldbackground=PANEL, rowheight=30, borderwidth=0)
        s.configure('Treeview.Heading', background=PANEL2, foreground=TEXT, font=('Segoe UI', 10, 'bold'))
        s.map('Treeview', background=[('selected', '#155477')])

    def _build(self):
        # Rebuilt dashboard based on the approved neon-blue hospital monitoring concept.
        self.root.configure(bg='#030d1b')
        self.root.option_add('*Font', ('Segoe UI', 10))
        self.root.title(f'{APP_NAME} | نسخه {VERSION}')
        self.root.geometry('1536x960')
        self.root.minsize(1080, 720)

        BG0 = '#030d1b'; BG1 = '#06172b'; PANEL = '#08213d'
        PANEL2 = '#0b2b4c'; EDGE = '#075b91'; CYAN2 = '#00cfff'
        WHITE = '#f1f8ff'; SUB = '#9dbbd4'; GREEN2 = '#00d58a'
        AMBER = '#ffbf36'; RED2 = '#ff5368'

        # Header / hospital identity
        header = tk.Frame(self.root, bg='#04172c', height=104,
                          highlightbackground='#075b91', highlightthickness=1)
        header.pack(fill='x', side='top')
        header.pack_propagate(False)
        brand_left = tk.Frame(header, bg='#04172c')
        brand_left.pack(side='left', padx=22, pady=13)
        tk.Label(brand_left, text='▣  JPD', bg='#04172c', fg=CYAN2,
                 font=('Segoe UI', 22, 'bold')).pack(anchor='w')
        tk.Label(brand_left, text='سلامت سیستم، آرامش شما', bg='#04172c',
                 fg=SUB, font=('Segoe UI', 9)).pack(anchor='w')
        brand = tk.Frame(header, bg='#04172c')
        brand.pack(side='right', padx=30, pady=12)
        tk.Label(brand, text='✚  بیمارستان آیت‌الله روحانی', bg='#04172c',
                 fg=WHITE, font=('Segoe UI', 22, 'bold')).pack(anchor='e')
        tk.Label(brand, text=f'JAVAD PC DOCTOR  •  نسخه {VERSION}  |  سامانه بررسی و پایش سخت‌افزار',
                 bg='#04172c', fg=CYAN2, font=('Segoe UI', 9, 'bold')).pack(anchor='e', pady=(3,0))
        self.status = tk.Label(header, text='● آماده بررسی', bg='#04172c',
                               fg=GREEN2, font=('Segoe UI', 10, 'bold'))
        self.status.pack(side='left', padx=28)

        shell = tk.Frame(self.root, bg=BG0)
        shell.pack(fill='both', expand=True)
        canvas = tk.Canvas(shell, bg=BG0, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        body = tk.Frame(canvas, bg=BG0, padx=14, pady=14)
        window_id = canvas.create_window((0,0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.bind_all('<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta/120), 'units'))

        # Navigation rail
        nav = tk.Frame(body, bg='#061b32', width=190, padx=12, pady=15,
                       highlightbackground=EDGE, highlightthickness=1)
        nav.pack(side='left', fill='y', padx=(0,12))
        nav.pack_propagate(False)
        tk.Label(nav, text='JPD', bg='#061b32', fg=CYAN2,
                 font=('Segoe UI', 27, 'bold')).pack(anchor='center', pady=(2,0))
        tk.Label(nav, text='CONTROL CENTER', bg='#061b32', fg=SUB,
                 font=('Segoe UI', 8, 'bold')).pack(anchor='center', pady=(0,18))
        tk.Label(nav, text='منوی اصلی', bg='#061b32', fg=WHITE,
                 font=('Segoe UI', 11, 'bold')).pack(anchor='e', padx=8, pady=(0,8))

        def nav_button(label, command, accent=WHITE, active=False):
            color = '#063e68' if active else '#0a2948'
            b = tk.Button(nav, text=label, command=command, bg=color, fg=accent,
                          activebackground='#07527c', activeforeground='white',
                          relief='flat', bd=0, anchor='e', padx=11, pady=12,
                          font=('Segoe UI', 10, 'bold'), cursor='hand2',
                          highlightthickness=1, highlightbackground=('#078ac0' if active else '#123b5d'))
            b.pack(fill='x', pady=4)
            return b

        nav_button('⌂   داشبورد اصلی', self.refresh_info, CYAN2, True)
        nav_button('◉   پایش هوشمند', self.start_stress_test, WHITE)
        nav_button('▦   تست کامل قطعات', self.start_full, WHITE)
        nav_button('▣   پردازنده CPU', lambda: self.start_one('CPU'), WHITE)
        nav_button('▤   حافظه RAM', lambda: self.start_one('RAM'), WHITE)
        nav_button('◈   کارت گرافیک', lambda: self.start_one('GPU'), WHITE)
        nav_button('▱   حافظه دیسک', lambda: self.start_one('DISK'), WHITE)
        nav_button('♨   دما و سنسورها', self.show_sensors, AMBER)
        nav_button('▤   گزارش‌ها', self.open_reports, WHITE)
        nav_button('⇩   ذخیره گزارش', self.save_report, CYAN2)
        tk.Frame(nav, bg='#164064', height=1).pack(fill='x', pady=15)
        tk.Label(nav, text='ارزیابی اولیه تکنسین', bg='#061b32',
                 fg=CYAN2, font=('Segoe UI', 10, 'bold')).pack(anchor='e', padx=5, pady=(0,8))
        self.assess_value = tk.Label(nav, text='هنوز انتخاب نشده', bg='#061b32',
                                     fg=SUB, font=('Segoe UI', 9))
        self.assess_value.pack(anchor='e', padx=5, pady=(0,8))
        assess_row = tk.Frame(nav, bg='#061b32')
        assess_row.pack(fill='x')
        self.assess_stable_btn = tk.Button(assess_row, text='✓ پایدار',
            command=lambda: self.set_assessment('پایدار'), bg='#07583f', fg='white',
            activebackground='#087d58', relief='flat', padx=5, pady=8,
            font=('Segoe UI', 9, 'bold'), cursor='hand2')
        self.assess_stable_btn.pack(side='right', expand=True, fill='x', padx=(3,0))
        self.assess_unstable_btn = tk.Button(assess_row, text='✕ ناپایدار',
            command=lambda: self.set_assessment('ناپایدار'), bg='#642b3a', fg='white',
            activebackground='#883448', relief='flat', padx=5, pady=8,
            font=('Segoe UI', 9, 'bold'), cursor='hand2')
        self.assess_unstable_btn.pack(side='left', expand=True, fill='x', padx=(0,3))
        tk.Label(nav, text='شدت پیش‌فرض پایش: سبک', bg='#061b32',
                 fg=SUB, font=('Segoe UI', 8)).pack(anchor='e', padx=5, pady=(14,2))

        # Main dashboard
        center = tk.Frame(body, bg=BG0)
        center.pack(side='left', fill='both', expand=True)
        title_row = tk.Frame(center, bg=BG0)
        title_row.pack(fill='x', pady=(0,10))
        tk.Label(title_row, text='داشبورد سلامت و عملکرد سیستم', bg=BG0,
                 fg=WHITE, font=('Segoe UI', 18, 'bold')).pack(side='right')
        tk.Label(title_row, text='LIVE HARDWARE MONITORING', bg=BG0,
                 fg=CYAN2, font=('Segoe UI', 8, 'bold')).pack(side='left', pady=7)

        # Four hardware summary panels, with live values only (no synthetic gauges).
        cards = tk.Frame(center, bg=BG0)
        cards.pack(fill='x', pady=(0,12))
        self.metric_values = {}
        self.metric_details = {}
        for key, title, symbol in [('CPU','پردازنده','CPU'),
                                   ('RAM','حافظه RAM','▤'),
                                   ('GPU','کارت گرافیک','◈'),
                                   ('DISK','حافظه دیسک','▱')]:
            card = tk.Frame(cards, bg='#082442', padx=12, pady=12,
                            highlightbackground='#075b91', highlightthickness=1)
            card.pack(side='left', fill='both', expand=True, padx=4)
            head = tk.Frame(card, bg='#082442')
            head.pack(fill='x')
            tk.Label(head, text=symbol, bg='#082442', fg=CYAN2,
                     font=('Segoe UI', 16, 'bold')).pack(side='left')
            tk.Label(head, text=title, bg='#082442', fg=WHITE,
                     font=('Segoe UI', 11, 'bold')).pack(side='right')
            value = tk.Label(card, text='در حال خواندن...', bg='#082442',
                             fg=CYAN2, font=('Segoe UI', 18, 'bold'),
                             anchor='e', justify='right')
            value.pack(fill='x', pady=(12,3))
            detail = tk.Label(card, text='اطلاعات سخت‌افزار', bg='#082442',
                              fg=SUB, font=('Segoe UI', 8), anchor='e',
                              justify='right', wraplength=210, height=2)
            detail.pack(fill='x')
            self.metric_values[key] = value
            self.metric_details[key] = detail

        # Main monitoring workspace: progress panel and honest overall status.
        mainrow = tk.Frame(center, bg=BG0)
        mainrow.pack(fill='x', pady=(0,12))
        monitor = tk.Frame(mainrow, bg='#061d36', padx=20, pady=17,
                           highlightbackground=CYAN2, highlightthickness=1)
        monitor.pack(side='left', fill='both', expand=True, padx=(0,8))
        side_status = tk.Frame(mainrow, bg='#082442', width=205, padx=14, pady=16,
                               highlightbackground='#075b91', highlightthickness=1)
        side_status.pack(side='right', fill='y', padx=(8,0))
        side_status.pack_propagate(False)
        monitor_head = tk.Frame(monitor, bg='#061d36')
        monitor_head.pack(fill='x')
        tk.Label(monitor_head, text='مرکز پایش هوشمند سیستم', bg='#061d36',
                 fg=WHITE, font=('Segoe UI', 16, 'bold')).pack(side='right')
        tk.Label(monitor_head, text='● LIVE', bg='#061d36', fg=GREEN2,
                 font=('Segoe UI', 9, 'bold')).pack(side='left', pady=5)
        self.summary = tk.Label(monitor,
            text='برای آغاز، نوع بررسی را از منوی سمت راست انتخاب کنید.',
            bg='#061d36', fg=SUB, font=('Segoe UI', 10),
            anchor='e', justify='right', wraplength=680)
        self.summary.pack(fill='x', pady=(15,8))
        self.stress_pct = tk.Label(monitor, text='۰٪', bg='#061d36',
                                   fg=CYAN2, font=('Segoe UI', 48, 'bold'))
        self.stress_pct.pack(anchor='center', pady=(2,0))
        tk.Label(monitor, text='درصد پیشرفت فرایند', bg='#061d36',
                 fg=SUB, font=('Segoe UI', 9)).pack(anchor='center')
        self.stress_bar = ttk.Progressbar(monitor, mode='determinate', maximum=100)
        self.stress_bar.pack(fill='x', pady=(12,12), ipady=8)
        self.stress_stage = tk.Label(monitor, text='آماده شروع  •  شدت سبک',
                                     bg='#061d36', fg=WHITE,
                                     font=('Segoe UI', 11, 'bold'), anchor='center')
        self.stress_stage.pack(fill='x', pady=(2,7))
        self.stress_time = tk.Label(monitor,
            text='زمان کل: ۳۰:۰۰   |   زمان باقی‌مانده: ۳۰:۰۰',
            bg='#061d36', fg=SUB, font=('Segoe UI', 10), anchor='center')
        self.stress_time.pack(fill='x')
        self.stress_detail = tk.Label(monitor,
            text='مراحل پایش: CPU  •  RAM  •  GPU  •  SSD / HDD',
            bg='#061d36', fg=CYAN2, font=('Segoe UI', 9), anchor='center')
        self.stress_detail.pack(fill='x', pady=(10,13))
        actions = tk.Frame(monitor, bg='#061d36')
        actions.pack(fill='x')
        self.stress_start_btn = tk.Button(actions, text='▶  شروع پایش ۳۰ دقیقه‌ای',
            command=self.start_stress_test, bg='#007b58', fg='white',
            activebackground='#00976b', relief='flat', padx=18, pady=11,
            font=('Segoe UI', 10, 'bold'), cursor='hand2')
        self.stress_start_btn.pack(side='right', expand=True, fill='x', padx=(0,5))
        self.stress_stop_btn = tk.Button(actions, text='■ توقف پایش',
            command=self.stop_stress_test, bg='#702d3c', fg='white',
            activebackground='#963c4e', relief='flat', padx=16, pady=11,
            font=('Segoe UI', 9, 'bold'), state='disabled', cursor='hand2')
        self.stress_stop_btn.pack(side='left', padx=(5,0))
        self.progress = ttk.Progressbar(monitor, mode='determinate', maximum=100)
        self.progress.pack(fill='x', pady=(12,2))
        self.progress_label = tk.Label(monitor, text='پیشرفت آزمون‌ها: ۰٪',
                                       bg='#061d36', fg=SUB, font=('Segoe UI', 8), anchor='e')
        self.progress_label.pack(fill='x')

        tk.Label(side_status, text='◈', bg='#082442', fg=CYAN2,
                 font=('Segoe UI', 35, 'bold')).pack(anchor='center', pady=(2,4))
        tk.Label(side_status, text='وضعیت کلی سیستم', bg='#082442',
                 fg=WHITE, font=('Segoe UI', 11, 'bold')).pack(anchor='center')
        self.overall_status = tk.Label(side_status, text='در انتظار بررسی',
                 bg='#123451', fg=AMBER, font=('Segoe UI', 13, 'bold'),
                 padx=8, pady=10)
        self.overall_status.pack(fill='x', pady=(12,14))
        for item in ('پردازنده','حافظه RAM','کارت گرافیک','حافظه دیسک'):
            tk.Label(side_status, text='○  '+item, bg='#082442',
                     fg=SUB, font=('Segoe UI', 9), anchor='e').pack(fill='x', pady=6)
        tk.Label(side_status, text='وضعیت فقط پس از آزمون واقعی مشخص می‌شود.',
                 bg='#082442', fg=AMBER, font=('Segoe UI', 8),
                 wraplength=170, justify='right').pack(side='bottom', fill='x', pady=(12,0))

        # Live temperature panel
        temp_panel = tk.Frame(center, bg='#071f38', padx=14, pady=11,
                              highlightbackground='#075b91', highlightthickness=1)
        temp_panel.pack(fill='x', pady=(0,12))
        temp_head = tk.Frame(temp_panel, bg='#071f38')
        temp_head.pack(fill='x')
        tk.Label(temp_head, text='دما و حسگرهای سخت‌افزار', bg='#071f38',
                 fg=WHITE, font=('Segoe UI', 11, 'bold')).pack(side='right')
        self.temp_source_label = tk.Label(temp_head, text='در حال شناسایی حسگرها...',
                 bg='#071f38', fg=SUB, font=('Segoe UI', 8))
        self.temp_source_label.pack(side='left')
        temp_grid = tk.Frame(temp_panel, bg='#071f38')
        temp_grid.pack(fill='x', pady=(8,0))
        self.temp_values = {}
        for key, caption in [('CPU','پردازنده'),('GPU','گرافیک'),
                             ('DISK','حافظه'),('BOARD','مادربرد')]:
            tile = tk.Frame(temp_grid, bg='#0b2b49', padx=10, pady=8,
                            highlightbackground='#16476b', highlightthickness=1)
            tile.pack(side='left', fill='both', expand=True, padx=4)
            tk.Label(tile, text=caption, bg='#0b2b49', fg=SUB,
                     font=('Segoe UI', 9, 'bold')).pack(anchor='center')
            value = tk.Label(tile, text='-- °C', bg='#0b2b49', fg=CYAN2,
                             font=('Segoe UI', 18, 'bold'))
            value.pack(anchor='center', pady=(3,1))
            detail = tk.Label(tile, text='در انتظار داده سنسور', bg='#0b2b49',
                              fg=SUB, font=('Segoe UI', 8), wraplength=175,
                              justify='center')
            detail.pack(anchor='center')
            self.temp_values[key] = (value, detail)
        self.temp_hint = tk.Label(temp_panel,
            text='برای دریافت دمای واقعی قطعات، سنسور سازگار یا LibreHardwareMonitor لازم است.',
            bg='#071f38', fg=AMBER, font=('Segoe UI', 8), anchor='e', justify='right')
        self.temp_hint.pack(fill='x', pady=(8,0))

        # Three lower columns: test stages, result log, alerts.
        lower = tk.Frame(center, bg=BG0)
        lower.pack(fill='both', expand=True, pady=(0,10))
        stages = tk.Frame(lower, bg='#082442', padx=12, pady=12,
                          highlightbackground='#075b91', highlightthickness=1)
        stages.pack(side='left', fill='both', expand=True, padx=(0,5))
        log = tk.Frame(lower, bg='#082442', padx=12, pady=12,
                       highlightbackground='#075b91', highlightthickness=1)
        log.pack(side='left', fill='both', expand=True, padx=5)
        alerts = tk.Frame(lower, bg='#082442', padx=12, pady=12,
                         highlightbackground='#075b91', highlightthickness=1)
        alerts.pack(side='left', fill='both', expand=True, padx=(5,0))
        tk.Label(stages, text='مراحل انجام کار', bg='#082442', fg=WHITE,
                 font=('Segoe UI', 11, 'bold')).pack(anchor='e')
        self.stage_list = tk.Label(stages,
            text='○  بررسی مشخصات سیستم\n○  بررسی پردازنده CPU\n○  بررسی حافظه RAM\n○  بررسی کارت گرافیک\n○  بررسی حافظه دیسک\n○  تولید گزارش نهایی',
            bg='#082442', fg=SUB, font=('Segoe UI', 9), justify='right',
            anchor='e')
        self.stage_list.pack(fill='both', expand=True, pady=(10,0))
        tk.Label(log, text='گزارش لحظه‌ای و نتایج', bg='#082442', fg=WHITE,
                 font=('Segoe UI', 11, 'bold')).pack(anchor='e', pady=(0,8))
        table_wrap = tk.Frame(log, bg='#082442')
        table_wrap.pack(fill='both', expand=True)
        cols = ('test','status','detail')
        self.tree = ttk.Treeview(table_wrap, columns=cols, show='headings', height=6)
        self.tree.heading('test', text='آزمون')
        self.tree.heading('status', text='وضعیت')
        self.tree.heading('detail', text='جزئیات')
        self.tree.column('test', width=85, anchor='center')
        self.tree.column('status', width=100, anchor='center')
        self.tree.column('detail', width=270, anchor='e')
        table_scroll = ttk.Scrollbar(table_wrap, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=table_scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        table_scroll.pack(side='right', fill='y')
        self.tree.tag_configure('ok', foreground=GREEN2)
        self.tree.tag_configure('warn', foreground=AMBER)
        self.tree.tag_configure('bad', foreground=RED2)
        tk.Label(alerts, text='هشدارها و نکات مهم  ⚠', bg='#082442',
                 fg=AMBER, font=('Segoe UI', 11, 'bold')).pack(anchor='e')
        self.alert_status = tk.Label(alerts,
            text='سامانه آماده است. هشدارهای واقعی پس از بررسی در این بخش نمایش داده می‌شوند.',
            bg='#082442', fg=SUB, font=('Segoe UI', 9),
            anchor='e', justify='right', wraplength=240)
        self.alert_status.pack(fill='both', expand=True, pady=(10,0))

        footer = tk.Frame(self.root, bg='#04172c', height=42,
                          highlightbackground='#075b91', highlightthickness=1)
        footer.pack(fill='x', side='bottom')
        footer.pack_propagate(False)
        tk.Label(footer, text='طراحی و برنامه‌نویسی: جواد مختاری',
                 bg='#04172c', fg=WHITE, font=('Segoe UI', 9, 'bold')).pack(side='right', padx=18, pady=9)
        tk.Label(footer, text='JAVAD PC DOCTOR  •  سامانه پایش سخت‌افزار',
                 bg='#04172c', fg=CYAN2, font=('Segoe UI', 8)).pack(side='left', padx=18, pady=9)

        # Apply the dark neon palette to native ttk widgets.
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TProgressbar', troughcolor='#102c49', background=CYAN2,
                        bordercolor='#102c49', lightcolor=CYAN2, darkcolor=CYAN2)
        style.configure('Treeview', background='#071c33', foreground=WHITE,
                        fieldbackground='#071c33', rowheight=27, borderwidth=0,
                        font=('Segoe UI', 9))
        style.configure('Treeview.Heading', background='#0b3558', foreground=WHITE,
                        font=('Segoe UI', 9, 'bold'), relief='flat')
        style.map('Treeview', background=[('selected', '#07527c')],
                  foreground=[('selected', WHITE)])

    def _panel_title(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=CYAN, font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(4,9))
    def big_button(self, parent, text, command, color):
        b=tk.Button(parent,text=text,command=command,bg=PANEL,fg=color,activebackground=PANEL2,activeforeground='white',relief='flat',bd=0,anchor='w',font=('Segoe UI',11,'bold'),padx=14,pady=12,cursor='hand2')
        b.pack(fill='x', pady=4)
    def set_assessment(self, value):
        self.technician_assessment = value
        if value == 'پایدار':
            self.assess_value.config(text='✓ پایدار', fg=GREEN)
            self.assess_stable_btn.config(relief='sunken', highlightthickness=1, highlightbackground=GREEN)
            self.assess_unstable_btn.config(relief='flat', highlightthickness=0)
        else:
            self.assess_value.config(text='✕ ناپایدار', fg=RED)
            self.assess_unstable_btn.config(relief='sunken', highlightthickness=1, highlightbackground=RED)
            self.assess_stable_btn.config(relief='flat', highlightthickness=0)

    def start_temperature_monitor(self):
        if getattr(self, '_temperature_thread_started', False):
            return
        self._temperature_thread_started = True
        threading.Thread(target=self._temperature_worker, daemon=True).start()

    def _temperature_worker(self):
        while not self.temp_monitor_stop.is_set():
            try:
                data = read_hardware_temperatures()
                self._ui(self.update_temperature_cards, data)
            except Exception:
                pass
            self.temp_monitor_stop.wait(3.0)

    def update_temperature_cards(self, data):
        if not hasattr(self, 'temp_values'):
            return
        source = data.get('SOURCE') or ''
        self.temp_source_label.config(
            text=('منبع: ' + source) if source else 'منبع سنسور متصل نیست')
        labels = {'CPU':'پردازنده','GPU':'گرافیک','DISK':'حافظه','BOARD':'مادربرد'}
        for key, (value_label, detail_label) in self.temp_values.items():
            readings = data.get(key) or []
            if readings:
                values = [v for _, v in readings]
                hottest = max(values)
                value_label.config(text=f'{hottest:.1f} °C',
                                   fg=RED if hottest >= 85 else (YELLOW if hottest >= 75 else CYAN))
                names = ' • '.join(f'{name.split("/")[-1]}: {temp:.1f}°' for name, temp in readings[:3])
                detail_label.config(text=names or labels[key])
            else:
                value_label.config(text='-- °C', fg=MUTED)
                detail_label.config(text='سنسور قابل شناسایی نیست')
        if source:
            self.temp_hint.config(text='دمای نمایش‌داده‌شده از سنسورهای شناسایی‌شده خوانده می‌شود؛ نبود سنسور به معنی صفر درجه نیست.',
                                  fg=MUTED)
        else:
            self.temp_hint.config(
                text='برای نمایش دمای واقعی CPU/GPU، LibreHardwareMonitor را اجرا کنید (ترجیحاً با دسترسی Administrator). سپس برنامه اینجا خودکار دماها را می‌خواند.',
                fg=YELLOW)

    def refresh_info(self):
        self.info = get_system_info()
        if hasattr(self, 'metric_values'):
            try:
                cpu = f"{psutil.cpu_percent(interval=None):.0f}٪ مصرف لحظه‌ای" if psutil else 'اطلاعات در دسترس نیست'
            except Exception:
                cpu = 'اطلاعات در دسترس نیست'
            try:
                ram = f"{psutil.virtual_memory().percent:.0f}٪ مصرف شده" if psutil else self.info.get('RAM total','نامشخص')
            except Exception:
                ram = self.info.get('RAM total','نامشخص')
            gpu = self.info.get('GPU','قابل شناسایی نبود')
            disk = self.info.get('Drives','قابل شناسایی نبود')
            self.metric_values['CPU'].config(text=cpu)
            self.metric_values['RAM'].config(text=ram)
            self.metric_values['GPU'].config(text=gpu)
            self.metric_values['DISK'].config(text=disk)
    def add_result(self, test, status, detail):
        tag = 'ok' if status == 'سالم' else ('bad' if status == 'خطا' else 'warn')
        self.tree.insert('', 'end', values=(test,status,detail), tags=(tag,))
        self.results.append({'test':test,'status':status,'detail':detail,'time':datetime.datetime.now().isoformat(timespec='seconds')})
    def set_progress(self, n, msg):
        self.progress['value']=n; self.progress_label.config(text=f'پیشرفت: {n}٪  |  {msg}'); self.root.update_idletasks()
    def start_full(self):
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running: return
        self.tree.delete(*self.tree.get_children()); self.results=[]; self.running=True; self.status.config(text='● در حال تست...',fg=YELLOW); self.summary.config(text='بررسی مرحله‌ای سخت‌افزار در حال اجراست...')
        threading.Thread(target=self._full_worker,daemon=True).start()
    def start_one(self, what):
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running: return
        self.tree.delete(*self.tree.get_children()); self.results=[]; self.running=True; self.status.config(text='● در حال تست...',fg=YELLOW)
        threading.Thread(target=self._one_worker,args=(what,),daemon=True).start()
    def _ui(self, fn, *args): self.root.after(0, lambda: fn(*args))
    def _full_worker(self):
        tests=[('SYS',self.test_system),('CPU',self.test_cpu),('RAM',self.test_ram),('DISK',self.test_disk),('GPU',self.test_gpu)]
        for i,(name,fn) in enumerate(tests,1):
            try:
                status,detail=fn()
            except Exception as e: status,detail='نیازمند بررسی',str(e)
            self._ui(self.add_result, name, status, detail); self._ui(self.set_progress, int(i*100/len(tests)), name)
        self._ui(self.finish)
    def _one_worker(self, what):
        fn={'CPU':self.test_cpu,'RAM':self.test_ram,'DISK':self.test_disk,'GPU':self.test_gpu}.get(what)
        try: status,detail=fn()
        except Exception as e: status,detail='نیازمند بررسی',str(e)
        self._ui(self.add_result,what,status,detail); self._ui(self.set_progress,100,what); self._ui(self.finish)
    def finish(self):
        self.running=False; self.status.config(text='● بررسی تمام شد',fg=GREEN); self.summary.config(text='بررسی به پایان رسید. نتیجه‌ها را در جدول ببینید و گزارش را ذخیره کنید.')
    def test_system(self):
        self.refresh_info(); return 'سالم', f"سیستم: {self.info.get('Computer')} | ویندوز: {self.info.get('OS')} | RAM: {self.info.get('RAM total','نامشخص')}"
    def test_cpu(self):
        # Read-only CPU information and temperature lookup; deliberately no stress loop.
        if not psutil:
            return 'نیازمند بررسی', 'کتابخانه psutil نصب نیست؛ هیچ تست فشاری اجرا نشد.'
        try:
            usage = psutil.cpu_percent(interval=0.25)
        except Exception:
            usage = None
        temps_text = ' | دمای واقعی CPU: در دسترس نیست'
        try:
            sensor_groups = psutil.sensors_temperatures()
            candidates = []
            for group, entries in sensor_groups.items():
                for entry in entries:
                    if entry.current is not None:
                        label = (entry.label or group).lower()
                        # Prefer explicitly CPU-labelled sensors; avoid presenting arbitrary board sensors as CPU.
                        if any(k in label for k in ('cpu', 'core', 'package', 'tctl', 'tdie')):
                            candidates.append(float(entry.current))
            if candidates:
                temps_text = f' | دمای واقعی CPU: {max(candidates):.1f}°C'
        except Exception:
            pass
        usage_text = f'{usage:.0f}٪' if usage is not None else 'نامشخص'
        return 'اطلاعات خوانده شد', f'بار لحظه‌ای CPU: {usage_text}{temps_text}. فقط خواندن اطلاعات؛ هیچ بار آزمایشی ایجاد نشد.'
    def test_ram(self):
        # Read-only memory capacity/availability query; no test allocation or memory load.
        if not psutil:
            return 'نیازمند بررسی', 'کتابخانه psutil نصب نیست؛ هیچ حافظه‌ای برای تست تخصیص داده نشد.'
        try:
            vm = psutil.virtual_memory()
            return 'اطلاعات خوانده شد', (f'حافظه کل: {vm.total/(1024**3):.1f} GB | '
                    f'در دسترس: {vm.available/(1024**3):.1f} GB | '
                    'فقط خواندن اطلاعات؛ تست نوشتن/خواندن RAM اجرا نشد.')
        except Exception as exc:
            return 'نیازمند بررسی', f'خواندن اطلاعات RAM ممکن نشد: {exc}. هیچ تست فشاری اجرا نشد.'
    def test_disk(self):
        health=disk_health();
        status='سالم' if ('Healthy' in health or 'خوب' in health) else 'نیازمند بررسی'
        if 'قابل دریافت نبود' in health: status='نیازمند بررسی'
        return status, health.replace('\n',' | ')
    def test_gpu(self):
        gpu=self.info.get('GPU','قابل شناسایی نبود')
        return ('سالم' if gpu!='قابل شناسایی نبود' else 'نیازمند بررسی', f"کارت گرافیک شناسایی‌شده: {gpu}. تست بار سه‌بعدی/VRAM در این نسخه اجرا نمی‌شود.")
    def _stress_ui(self, percent, stage, remaining, detail=''):
        percent=max(0,min(100,int(percent)))
        digits=str(percent).translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))
        self.stress_pct.config(text=digits+'٪')
        self.stress_bar['value'] = percent
        mins,secs=divmod(max(0,int(remaining)),60)
        self.stress_stage.config(text=stage)
        self.stress_time.config(text=f'زمان کل: ۳۰:۰۰  |  باقی‌مانده: {mins:02d}:{secs:02d}')
        if detail: self.stress_detail.config(text=detail)
        self.progress['value']=percent
        self.progress_label.config(text=f'پیشرفت پایش: {digits}٪  |  {stage}')

    def start_stress_test(self):
        """Run a 30-minute visual-only simulation. It deliberately creates no hardware load."""
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running or self.stress_running:
            return
        if not messagebox.askyesno('پایش هوشمند ۳۰ دقیقه‌ای',
            'این فرایند ۳۰ دقیقه طول می‌کشد و با شدت سبک اجرا می‌شود.\n'
            'هیچ پردازش سنگین، تخصیص آزمایشی RAM، تست دیسک یا بار گرافیکی اجرا نمی‌شود.\n'
            'این فرایند به‌تنهایی تأیید سلامت قطعات محسوب نمی‌شود.\n\n'
            'شروع شود؟'):
            return
        self.tree.delete(*self.tree.get_children()); self.results=[]
        self.stress_cancel.clear(); self.running=True; self.stress_running=True
        self.status.config(text='● پایش مرحله‌ای در حال اجرا',fg=YELLOW)
        self.summary.config(text='پایش مرحله‌ای در حال اجراست؛ شدت سبک و بدون بار مصنوعی.')
        self.stress_start_btn.config(state='disabled'); self.stress_stop_btn.config(state='normal')
        self._stress_ui(0,'آماده‌سازی',1800,'پایش مرحله‌ای با شدت سبک')
        threading.Thread(target=self._stress_worker,daemon=True).start()

    def stop_stress_test(self):
        if self.stress_running:
            self.stress_cancel.set()
            self.stress_stage.config(text='درخواست توقف پایش...')
            self.stress_stop_btn.config(state='disabled')

    def _stress_worker(self):
        # Pure timer/UI simulation: no CPU benchmark, RAM allocation, disk scan, GPU rendering,
        # or sensor polling is performed during this 30-minute sequence.
        total = 30 * 60
        phases = [
            ('مرحله پردازنده (CPU)', 6 * 60, 'نمایش گرافیکی مرحله CPU • هیچ بار پردازشی تولید نمی‌شود'),
            ('مرحله حافظه (RAM)', 6 * 60, 'نمایش مرحله RAM • هیچ حافظه‌ای برای آزمون تخصیص داده نمی‌شود'),
            ('مرحله گرافیک (GPU)', 6 * 60, 'نمایش مرحله GPU • هیچ رندر یا تست سه‌بعدی اجرا نمی‌شود'),
            ('مرحله دیسک (SSD / HDD)', 6 * 60, 'نمایش مرحله دیسک • هیچ اسکن یا عملیات خواندن/نوشتن انجام نمی‌شود'),
            ('پایش و جمع‌بندی مرحله‌ای', 6 * 60, 'نمایش جمع‌بندی • هیچ حسگری در این حالت به‌صورت دوره‌ای خوانده نمی‌شود'),
        ]
        started = time.monotonic()
        outcome = 'پایش تکمیل شد'
        current_phase = 0
        try:
            while True:
                if self.stress_cancel.is_set():
                    outcome = 'پایش توسط کاربر متوقف شد'
                    break
                elapsed = min(total, int(time.monotonic() - started))
                remaining = max(0, total - elapsed)
                pct = min(100, int(elapsed * 100 / total))
                cumulative = 0
                phase_idx = len(phases) - 1
                for idx, (_, duration, _) in enumerate(phases):
                    if elapsed < cumulative + duration:
                        phase_idx = idx
                        break
                    cumulative += duration
                current_phase = phase_idx
                phase_name, _, detail = phases[phase_idx]
                self._ui(self._stress_ui, pct, phase_name, remaining, detail + ' • حالت مرحله‌ای')
                if remaining <= 0:
                    break
                self.stress_cancel.wait(1.0)
            elapsed = int(time.monotonic() - started)
            if outcome == 'پایش تکمیل شد':
                self._ui(self._stress_ui, 100, 'پایش ۳۰ دقیقه‌ای تکمیل شد', 0,
                         'مراحل پایش به پایان رسید؛ نتایج قطعات در آزمون‌های مستقل ثبت می‌شوند')
            status = 'پایش'
            detail = (f'{outcome}؛ مدت اجرا {elapsed//60:02d}:{elapsed%60:02d}. '
                      'این فرآیند صرفاً مرحله‌ای بود؛ هیچ فشار یا تست واقعی روی CPU، RAM، GPU یا دیسک انجام نشد. '
                      'نتیجه، سلامت قطعات را تأیید یا رد نمی‌کند.')
            # تایمر پایش به‌عنوان نتیجه سلامت قطعات ثبت نمی‌شود
            self._ui(self._stress_finish, outcome, status)
        except Exception as exc:
            # خطای رابط پایش در وضعیت برنامه نمایش داده می‌شود
            self._ui(self._stress_finish, 'پایش با خطا متوقف شد', 'نیازمند بررسی')

    def _stress_finish(self, outcome, status):
        self.running=False; self.stress_running=False
        self.stress_start_btn.config(state='normal'); self.stress_stop_btn.config(state='disabled')
        self.status.config(text='● '+outcome,fg=YELLOW)
        self.summary.config(text='پایش پایان یافت؛ نتیجه مرحله‌ای است و سلامت واقعی قطعات را نشان نمی‌دهد.')
        if outcome == 'پایش تکمیل شد':
            self._stress_ui(100, 'پایش تکمیل شد', 0,
                            'پایش پایان یافت • نتایج آزمون‌های مستقل در جدول ثبت می‌شوند')

    def show_sensors(self):
        if psutil:
            try:
                ts=psutil.sensors_temperatures()
                lines=[]
                for name,arr in ts.items():
                    for v in arr: lines.append(f'{name} / {v.label or "Sensor"}: {v.current}°C')
                if lines: messagebox.showinfo('دما و سنسورها','\n'.join(lines))
                else: messagebox.showinfo('دما و سنسورها','ویندوز/سخت‌افزار دما را از طریق psutil در دسترس قرار نداده است. برای نمایش مطمئن‌تر، LibreHardwareMonitor را کنار برنامه قرار دهید؛ این نسخه هنوز اتصال مستقیم به آن را ندارد.')
            except Exception: messagebox.showinfo('دما و سنسورها','خواندن سنسورهای دما توسط این سیستم پشتیبانی نشد.')
        else: messagebox.showinfo('دما و سنسورها','کتابخانه لازم نصب نیست.')
    def save_report(self):
        self.refresh_info()
        stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); name=f"Report_{self.info.get('Computer','PC')}_{stamp}.txt".replace(' ','_')
        path=REPORTS/name
        lines=[APP_NAME+f' v{VERSION}', 'زمان: '+datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '\n=== ارزیابی اولیه تکنسین (یادداشت داخلی) ===', self.technician_assessment, 'این ارزیابی دستی است و نتیجه اندازه‌گیری قطعات محسوب نمی‌شود.', '\n=== مشخصات سیستم ===']
        lines += [f'{k}: {v}' for k,v in self.info.items()]
        lines += ['\n=== نتایج تست ===']
        lines += [f"{r['test']} | {r['status']} | {r['detail']} | {r['time']}" for r in self.results]
        lines += ['\nمحدودیت‌ها: فرایند ۳۰ دقیقه‌ای زمان‌بندی پایش مرحله‌ای است. بررسی CPU و RAM در این نسخه فقط خواندنی است و هیچ بار آزمایشی ایجاد نمی‌کند. ارزیابی دستی تکنسین جدا از نتایج واقعی نگهداری می‌شود و آن‌ها را تغییر نمی‌دهد. دمای CPU/GPU در همه سیستم‌ها قابل خواندن نیست؛ مقدار ناموجود نباید حدس زده شود.']
        path.write_text('\n'.join(lines),encoding='utf-8-sig')
        messagebox.showinfo('گزارش ذخیره شد',f'گزارش ذخیره شد:\n{path}')
    def open_reports(self):
        REPORTS.mkdir(exist_ok=True)
        if os.name=='nt': os.startfile(str(REPORTS))
        else: messagebox.showinfo('گزارش‌ها',str(REPORTS))


def main():
    root=tk.Tk()
    # Modern vector splash screen (no legacy banner artwork).
    root.withdraw()
    splash=tk.Toplevel(root); splash.title(APP_NAME); splash.configure(bg='#071629'); splash.overrideredirect(True)
    sw, sh = 760, 300
    x=(splash.winfo_screenwidth()-sw)//2; y=(splash.winfo_screenheight()-sh)//2
    splash.geometry(f'{sw}x{sh}+{x}+{y}')
    art=tk.Canvas(splash,width=sw,height=sh-62,bg='#071629',highlightthickness=0)
    art.pack(fill='x')
    # Subtle technical grid and luminous circuit motif.
    for gx in range(18,sw,36):
        art.create_line(gx,0,gx,sh-62,fill='#102b46',width=1)
    for gy in range(16,sh-62,28):
        art.create_line(0,gy,sw,gy,fill='#102b46',width=1)
    art.create_oval(520,18,720,218,outline='#124b68',width=2)
    art.create_oval(548,46,692,190,outline='#146b83',width=2)
    art.create_oval(578,76,662,160,outline='#20d5ef',width=2)
    art.create_text(620,118,text='JPD',fill='#20d5ef',font=('Segoe UI',28,'bold'))
    art.create_text(42,62,text='JAVAD PC DOCTOR',anchor='w',fill='#20d5ef',
                    font=('Segoe UI',12,'bold'))
    art.create_text(42,112,text='بیمارستان آیت‌الله روحانی',anchor='w',fill='#ffffff',
                    font=('Segoe UI',23,'bold'))
    art.create_text(42,154,text='سامانه هوشمند بررسی و پایش سخت‌افزار',anchor='w',
                    fill='#a8c3dc',font=('Segoe UI',12))
    art.create_line(42,190,470,190,fill='#1b526e',width=2)
    art.create_text(42,216,text='طراحی و برنامه‌نویسی: جواد مختاری',anchor='w',
                    fill='#a8c3dc',font=('Segoe UI',10))
    tk.Label(splash,text='در حال آماده‌سازی محیط عیب‌یابی...',
             font=('Segoe UI',10),fg=TEXT,bg='#071629').pack(pady=(7,3))
    bar=ttk.Progressbar(splash,mode='indeterminate',length=660); bar.pack(pady=(0,12)); bar.start(12)
    def show_main():
        try: splash.destroy()
        except Exception: pass
        root.deiconify(); DoctorApp(root)
    root.after(1700, show_main)
    root.mainloop()
if __name__=='__main__': main()
