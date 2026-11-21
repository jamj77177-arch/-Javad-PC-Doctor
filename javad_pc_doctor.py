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

try:
    from diagnostic_engine import run_30_minute_diagnostic
except Exception:
    run_30_minute_diagnostic = None

APP_NAME = 'Javad PC Doctor'
VERSION = '1.7.0'
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


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def get_hardware_inventory():
    """Read hardware identity/capacity from Windows CIM. Missing fields stay explicitly unavailable."""
    info = {'CPU details': 'شناسایی نشد', 'RAM modules': 'شناسایی نشد',
            'GPU details': 'شناسایی نشد', 'Physical disks': 'شناسایی نشد',
            'Motherboard details': 'شناسایی نشد', 'BIOS': 'شناسایی نشد',
            'Windows disk health': 'قابل دریافت نیست'}
    if os.name != 'nt':
        return info
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
$cpu=Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize
$ram=Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed,SMBIOSMemoryType,DeviceLocator
$gpu=Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor,CurrentHorizontalResolution,CurrentVerticalResolution
$disk=Get-CimInstance Win32_DiskDrive | Select-Object Model,Size,InterfaceType,MediaType,FirmwareRevision,Status
$board=Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product,Version
$bios=Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate
$health=Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus,MediaType,Size
$result=@{CPU=@($cpu);RAM=@($ram);GPU=@($gpu);Disks=@($disk);Board=@($board);BIOS=@($bios);DiskHealth=@($health)}
$result | ConvertTo-Json -Depth 5 -Compress
"""
    try:
        raw = run_powershell(ps, timeout=18)
        data = json.loads(raw) if raw else {}
        cpus = _as_list(data.get('CPU'))
        if cpus:
            parts = []
            for c in cpus:
                parts.append("{} | {} هسته / {} رشته | فرکانس پایه {} MHz | کش L2={} KB / L3={} KB".format(
                    c.get('Name') or 'مدل نامشخص', c.get('NumberOfCores') or '?',
                    c.get('NumberOfLogicalProcessors') or '?', c.get('MaxClockSpeed') or '?',
                    c.get('L2CacheSize') or '?', c.get('L3CacheSize') or '?'))
            info['CPU details'] = ' ؛ '.join(parts)
        modules = _as_list(data.get('RAM'))
        if modules:
            parts = []
            for m in modules:
                cap = m.get('Capacity')
                cap_text = f"{int(cap)/(1024**3):.1f} GB" if cap else 'ظرفیت نامشخص'
                speed = m.get('ConfiguredClockSpeed') or m.get('Speed') or 'نامشخص'
                maker = (m.get('Manufacturer') or '').strip() or 'سازنده نامشخص'
                part = (m.get('PartNumber') or '').strip() or 'شماره قطعه نامشخص'
                slot = m.get('DeviceLocator') or 'اسلات نامشخص'
                parts.append(f"{slot}: {cap_text} | {speed} MHz | {maker} | {part}")
            info['RAM modules'] = ' ؛ '.join(parts)
        gpus = _as_list(data.get('GPU'))
        if gpus:
            parts = []
            for g in gpus:
                mem = g.get('AdapterRAM')
                mem_text = f"{int(mem)/(1024**3):.1f} GB" if mem else 'حافظه اختصاصی از ویندوز گزارش نشد'
                parts.append(f"{g.get('Name') or 'مدل نامشخص'} | VRAM گزارش‌شده: {mem_text} | درایور: {g.get('DriverVersion') or 'نامشخص'} | پردازنده: {g.get('VideoProcessor') or 'نامشخص'}")
            info['GPU details'] = ' ؛ '.join(parts)
        disks = _as_list(data.get('Disks'))
        if disks:
            parts = []
            for d in disks:
                size = d.get('Size')
                size_text = f"{int(size)/(1024**3):.0f} GB" if size else 'ظرفیت نامشخص'
                parts.append(f"{d.get('Model') or 'مدل نامشخص'} | {size_text} | رابط: {d.get('InterfaceType') or 'نامشخص'} | نوع گزارش‌شده: {d.get('MediaType') or 'نامشخص'} | وضعیت CIM: {d.get('Status') or 'نامشخص'}")
            info['Physical disks'] = ' ؛ '.join(parts)
        boards = _as_list(data.get('Board'))
        if boards:
            b = boards[0]
            info['Motherboard details'] = f"{b.get('Manufacturer') or 'سازنده نامشخص'} | {b.get('Product') or 'مدل نامشخص'} | نسخه: {b.get('Version') or 'نامشخص'}"
        bioses = _as_list(data.get('BIOS'))
        if bioses:
            b = bioses[0]
            info['BIOS'] = f"{b.get('Manufacturer') or 'سازنده نامشخص'} | {b.get('SMBIOSBIOSVersion') or 'نسخه نامشخص'} | تاریخ: {b.get('ReleaseDate') or 'نامشخص'}"
        health = _as_list(data.get('DiskHealth'))
        if health:
            info['Windows disk health'] = ' ؛ '.join(
                f"{d.get('FriendlyName') or 'Disk'}: سلامت ویندوز={d.get('HealthStatus') or 'نامشخص'}، وضعیت={d.get('OperationalStatus') or 'نامشخص'}، نوع={d.get('MediaType') or 'نامشخص'}"
                for d in health)
    except Exception as exc:
        info['Inventory note'] = f'خواندن جزئیات CIM ناموفق بود: {exc}'
    return info


def get_system_info():
    info = {'Computer': socket.gethostname(), 'OS': platform.platform(),
            'Architecture': platform.machine(), 'Processor': platform.processor() or 'شناسایی نشد',
            'Logical CPUs': os.cpu_count() or 'شناسایی نشد'}
    if psutil:
        try:
            vm = psutil.virtual_memory()
            info['RAM total'] = f'{vm.total / (1024**3):.1f} GB'
            info['RAM available'] = f'{vm.available / (1024**3):.1f} GB'
            info['Boot time'] = datetime.datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M')
        except Exception:
            info['RAM total'] = 'شناسایی نشد'
    inventory = get_hardware_inventory()
    info.update(inventory)
    info['GPU'] = info.get('GPU details', 'شناسایی نشد')
    info['Drives'] = info.get('Physical disks', 'شناسایی نشد')
    info['Motherboard'] = info.get('Motherboard details', 'شناسایی نشد')
    return info

def disk_health():
    # Windows reports a coarse health status; not a full SMART surface scan.
    out = run_powershell("Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus,MediaType | ConvertTo-Json -Compress")
    if not out:
        return 'وضعیت سلامت دیسک از ویندوز قابل دریافت نیست؛ مقدار نامشخص است.'
    try:
        data = json.loads(out)
        if isinstance(data, dict): data = [data]
        data = [d for d in data if isinstance(d, dict)]
        if not data:
            return 'ویندوز هیچ دیسک فیزیکی قابل گزارش نکرد؛ وضعیت سلامت نامشخص است.'
        return '\n'.join(
            f"{d.get('FriendlyName') or 'نام دیسک شناسایی نشد'}: سلامت={d.get('HealthStatus') or 'نامشخص'}، وضعیت={d.get('OperationalStatus') or 'نامشخص'}، نوع={d.get('MediaType') or 'نامشخص'}"
            for d in data)
    except Exception:
        return 'پاسخ سلامت دیسک قابل تفسیر نیست؛ وضعیت نامشخص است.'



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
        self.results = []; self.running = False; self.stress_running = False; self.stress_cancel = threading.Event(); self.info = get_system_info(); self.technician_assessment = 'انتخاب نشده'; self.stress_temp_max = {}; self.stress_temp_samples = {}
        self._style(); self._build(); self.refresh_info(); self.temp_monitor_stop = threading.Event(); self.root.after(700, self.start_temperature_monitor)

    def _style(self):
        s = ttk.Style(); s.theme_use('clam')
        s.configure('TProgressbar', troughcolor='#183653', background=CYAN, bordercolor='#183653', lightcolor=CYAN, darkcolor=CYAN)
        s.configure('Treeview', background=PANEL, foreground=TEXT, fieldbackground=PANEL, rowheight=30, borderwidth=0)
        s.configure('Treeview.Heading', background=PANEL2, foreground=TEXT, font=('Segoe UI', 10, 'bold'))
        s.map('Treeview', background=[('selected', '#155477')])

    def _build(self):
        # Compact, single-screen neon dashboard. No page-level scrolling.
        C = {
            'bg':'#050d1a', 'header':'#081a2d', 'panel':'#0b2037',
            'panel2':'#102b46', 'edge':'#164766', 'cyan':'#19d9f2',
            'blue':'#168bd0', 'white':'#eff8ff', 'muted':'#91abc2',
            'green':'#20d69a', 'amber':'#ffbf52', 'red':'#ff6175',
            'track':'#18334d'
        }
        self.ui_colors = C
        self.root.configure(bg=C['bg'])
        self.root.option_add('*Font', ('Segoe UI', 9))
        self.root.title(f'{APP_NAME} | نسخه {VERSION}')
        self.root.geometry('1440x900')
        self.root.minsize(1024, 680)
        try: self.root.state('zoomed')
        except Exception: pass

        style = ttk.Style()
        try: style.theme_use('clam')
        except Exception: pass
        style.configure('TProgressbar', troughcolor=C['track'], background=C['cyan'],
                        bordercolor=C['track'], lightcolor=C['cyan'], darkcolor=C['cyan'])
        style.configure('Treeview', background='#081a2d', foreground=C['white'],
                        fieldbackground='#081a2d', rowheight=23, borderwidth=0,
                        font=('Segoe UI', 8))
        style.configure('Treeview.Heading', background='#123653', foreground=C['cyan'],
                        font=('Segoe UI', 8, 'bold'), relief='flat')
        style.map('Treeview', background=[('selected','#155477')],
                  foreground=[('selected',C['white'])])

        def panel(parent, bg=None, padx=12, pady=10):
            return tk.Frame(parent, bg=bg or C['panel'], padx=padx, pady=pady,
                            highlightbackground=C['edge'], highlightthickness=1)
        def label(parent, text, size=9, color=None, bold=False, **kw):
            return tk.Label(parent, text=text, bg=kw.pop('bg', C['panel']),
                            fg=color or C['white'],
                            font=('Segoe UI', size, 'bold' if bold else 'normal'), **kw)
        def section(parent, title, hint=None):
            h=tk.Frame(parent,bg=C['panel']); h.pack(fill='x',pady=(0,7))
            label(h,title,10,C['white'],True).pack(side='right')
            if hint: label(h,hint,8,C['muted']).pack(side='left')
            return h
        def action(parent, text, command, color, fg='white', **kw):
            return tk.Button(parent,text=text,command=command,bg=color,fg=fg,
                activebackground=color,activeforeground=fg,relief='flat',bd=0,
                padx=10,pady=7,font=('Segoe UI',8,'bold'),cursor='hand2',**kw)

        # Header
        header=tk.Frame(self.root,bg=C['header'],height=68,
                        highlightbackground=C['edge'],highlightthickness=1)
        header.pack(side='top',fill='x'); header.pack_propagate(False)
        brand=tk.Frame(header,bg=C['header']); brand.pack(side='left',padx=20,pady=8)
        label(brand,'JPD  ◈',21,C['cyan'],True,bg=C['header']).pack(anchor='w')
        label(brand,'HARDWARE DIAGNOSTIC CENTER',7,C['muted'],True,bg=C['header']).pack(anchor='w')
        hospital=tk.Frame(header,bg=C['header']); hospital.pack(side='right',padx=22,pady=8)
        label(hospital,'✚  بیمارستان آیت‌الله روحانی',17,C['white'],True,bg=C['header']).pack(anchor='e')
        label(hospital,'برنامه‌نویس: جواد مختاری',9,C['white'],True,bg=C['header']).pack(anchor='e')
        label(hospital,f'JAVAD PC DOCTOR  •  نسخه {VERSION}  |  سامانه هوشمند پایش سخت‌افزار',8,C['cyan'],True,bg=C['header']).pack(anchor='e')
        self.status=label(header,'● آماده بررسی',9,C['green'],True,bg=C['header'])
        self.status.pack(side='left',padx=22)

        # Main shell: fixed navigation rail + dashboard workspace.
        shell=tk.Frame(self.root,bg=C['bg']); shell.pack(fill='both',expand=True,padx=10,pady=9)
        nav=tk.Frame(shell,bg='#07182a',width=174,padx=10,pady=12,
                     highlightbackground=C['edge'],highlightthickness=1)
        nav.pack(side='left',fill='y',padx=(0,10)); nav.pack_propagate(False)
        label(nav,'JPD',25,C['cyan'],True,bg='#07182a').pack(pady=(2,0))
        label(nav,'CONTROL PANEL',7,C['muted'],True,bg='#07182a').pack(pady=(0,14))
        label(nav,'ناوبری اصلی',9,C['muted'],True,bg='#07182a').pack(anchor='e',padx=5,pady=(0,5))
        def nav_button(text, command, active=False, accent=None):
            b=action(nav,text,command,'#123d5c' if active else '#0b2944',accent or (C['cyan'] if active else C['white']))
            b.pack(fill='x',pady=3); return b
        nav_button('⌂   داشبورد',self.refresh_info,True)
        nav_button('◉   پایش هوشمند',self.start_stress_test)
        nav_button('▦   تست کامل قطعات',self.start_full)
        nav_button('▣   پردازنده CPU',lambda:self.start_one('CPU'))
        nav_button('▤   حافظه RAM',lambda:self.start_one('RAM'))
        nav_button('◈   کارت گرافیک',lambda:self.start_one('GPU'))
        nav_button('▱   حافظه دیسک',lambda:self.start_one('DISK'))
        nav_button('♨   دما و سنسورها',self.show_sensors,accent=C['amber'])
        nav_button('▤   گزارش‌ها',self.open_reports)
        nav_button('⇩   ذخیره گزارش',self.save_report,accent=C['cyan'])
        tk.Frame(nav,bg=C['edge'],height=1).pack(fill='x',pady=10)
        label(nav,'ارزیابی اولیه تکنسین',9,C['cyan'],True,bg='#07182a').pack(anchor='e',padx=4)
        self.assess_value=label(nav,'هنوز انتخاب نشده',8,C['muted'],bg='#07182a')
        self.assess_value.pack(anchor='e',padx=4,pady=(5,7))
        assess=tk.Frame(nav,bg='#07182a'); assess.pack(fill='x')
        self.assess_stable_btn=action(assess,'✓ پایدار',lambda:self.set_assessment('پایدار'),'#07583f')
        self.assess_stable_btn.pack(side='right',expand=True,fill='x',padx=(2,0))
        self.assess_unstable_btn=action(assess,'✕ ناپایدار',lambda:self.set_assessment('ناپایدار'),'#682c3c')
        self.assess_unstable_btn.pack(side='left',expand=True,fill='x',padx=(0,2))
        label(nav,'شدت پیش‌فرض پایش: سبک',7,C['muted'],bg='#07182a').pack(side='bottom',pady=5)

        center=tk.Frame(shell,bg=C['bg']); center.pack(side='left',fill='both',expand=True)
        titlebar=tk.Frame(center,bg=C['bg']); titlebar.pack(fill='x',pady=(0,7))
        label(titlebar,'داشبورد سلامت و عملکرد سیستم',15,C['white'],True,bg=C['bg']).pack(side='right')
        label(titlebar,'● LIVE HARDWARE MONITORING',8,C['cyan'],True,bg=C['bg']).pack(side='left',pady=5)

        # Four live hardware cards
        cards=tk.Frame(center,bg=C['bg']); cards.pack(fill='x',pady=(0,8))
        self.metric_values={}; self.metric_details={}; self.metric_bars={}
        for key,title,symbol in [('CPU','پردازنده','CPU'),('RAM','حافظه RAM','RAM'),
                                 ('GPU','پردازنده گرافیکی','GPU'),('DISK','حافظه ذخیره‌سازی','SSD')]:
            card=panel(cards,'#0a223b',10,8); card.pack(side='left',fill='both',expand=True,padx=3)
            top=tk.Frame(card,bg='#0a223b'); top.pack(fill='x')
            label(top,symbol,12,C['cyan'],True,bg='#0a223b').pack(side='left')
            label(top,title,9,C['white'],True,bg='#0a223b').pack(side='right')
            val=label(card,'--',17,C['cyan'],True,bg='#0a223b',anchor='e')
            val.pack(fill='x',pady=(5,0)); self.metric_values[key]=val
            detail=label(card,'در حال دریافت اطلاعات...',7,C['muted'],bg='#0a223b',anchor='e')
            detail.pack(fill='x',pady=(1,4)); self.metric_details[key]=detail
            bar=ttk.Progressbar(card,mode='determinate',maximum=100)
            bar.pack(fill='x',ipady=2); self.metric_bars[key]=bar

        # Central monitoring and compact system status
        mainrow=tk.Frame(center,bg=C['bg']); mainrow.pack(fill='x',pady=(0,8))
        monitor=panel(mainrow,'#081d33',15,10)
        monitor.pack(side='left',fill='both',expand=True,padx=(0,5))
        side=panel(mainrow,'#0a223b',12,10); side.pack(side='right',fill='y',padx=(5,0))
        mh=tk.Frame(monitor,bg='#081d33'); mh.pack(fill='x')
        label(mh,'مرکز فرماندهی پایش هوشمند',12,C['white'],True,bg='#081d33').pack(side='right')
        label(mh,'● SYSTEM MONITOR',8,C['green'],True,bg='#081d33').pack(side='left')
        self.summary=label(monitor,'برای آغاز بررسی، یکی از آزمون‌ها را انتخاب کنید.',8,C['muted'],bg='#081d33',anchor='e',justify='right')
        self.summary.pack(fill='x',pady=(5,3))
        self.stress_pct=label(monitor,'۰٪',35,C['cyan'],True,bg='#081d33')
        self.stress_pct.pack(anchor='center')
        label(monitor,'درصد پیشرفت فرایند',7,C['muted'],bg='#081d33').pack(anchor='center')
        self.stress_bar=ttk.Progressbar(monitor,mode='determinate',maximum=100)
        self.stress_bar.pack(fill='x',pady=(5,5),ipady=5)
        self.stress_stage=label(monitor,'آماده شروع  •  شدت سبک',9,C['white'],True,bg='#081d33')
        self.stress_stage.pack(fill='x',anchor='center')
        self.stress_time=label(monitor,'زمان کل: ۳۰:۰۰  |  باقی‌مانده: ۳۰:۰۰',8,C['muted'],bg='#081d33')
        self.stress_time.pack(fill='x',pady=(2,4))
        self.stress_detail=label(monitor,'CPU  •  RAM  •  GPU  •  SSD / HDD',7,C['cyan'],bg='#081d33')
        self.stress_detail.pack(fill='x',pady=(0,6))
        controls=tk.Frame(monitor,bg='#081d33'); controls.pack(fill='x')
        self.stress_start_btn=action(controls,'▶  شروع پایش ۳۰ دقیقه‌ای',self.start_stress_test,'#087b5b')
        self.stress_start_btn.pack(side='right',fill='x',expand=True,padx=(0,4))
        self.stress_stop_btn=action(controls,'■ توقف',self.stop_stress_test,'#702d3c')
        self.stress_stop_btn.pack(side='left',padx=(4,0)); self.stress_stop_btn.config(state='disabled')
        self.progress=ttk.Progressbar(monitor,mode='determinate',maximum=100)
        self.progress.pack(fill='x',pady=(6,1))
        self.progress_label=label(monitor,'پیشرفت آزمون‌ها: ۰٪',7,C['muted'],bg='#081d33',anchor='e')
        self.progress_label.pack(fill='x')

        label(side,'◈',26,C['cyan'],True).pack()
        label(side,'وضعیت کلی سیستم',9,C['white'],True).pack()
        self.overall_status=label(side,'در انتظار بررسی',11,C['amber'],True,bg='#15344d')
        self.overall_status.pack(fill='x',pady=(7,8),ipady=5)
        for item in ('پردازنده','حافظه RAM','کارت گرافیک','حافظه دیسک'):
            label(side,'○  '+item,8,C['muted'],bg=C['panel']).pack(anchor='e',pady=3)
        label(side,'نتیجه فقط بر پایه داده و آزمون واقعی اعلام می‌شود.',7,C['amber'],bg=C['panel'],wraplength=155,justify='right').pack(side='bottom',pady=(8,0))

        # Temperature strip; unavailable sensors stay explicitly unavailable.
        temps=panel(center,'#091f36',10,7); temps.pack(fill='x',pady=(0,8))
        th=tk.Frame(temps,bg='#091f36'); th.pack(fill='x')
        label(th,'دما و حسگرهای سخت‌افزار',9,C['white'],True,bg='#091f36').pack(side='right')
        self.temp_source_label=label(th,'در حال شناسایی حسگرها...',7,C['muted'],bg='#091f36')
        self.temp_source_label.pack(side='left')
        tg=tk.Frame(temps,bg='#091f36'); tg.pack(fill='x',pady=(5,0))
        self.temp_values={}
        for key,caption in [('CPU','پردازنده'),('GPU','گرافیک'),('DISK','حافظه'),('BOARD','مادربرد')]:
            tile=tk.Frame(tg,bg='#102b46',padx=8,pady=4); tile.pack(side='left',fill='both',expand=True,padx=3)
            label(tile,caption,7,C['muted'],True,bg='#102b46').pack(anchor='center')
            value=label(tile,'-- °C',13,C['cyan'],True,bg='#102b46'); value.pack(anchor='center')
            detail=label(tile,'سنسور شناسایی نشده',6,C['muted'],bg='#102b46'); detail.pack(anchor='center')
            self.temp_values[key]=(value,detail)
        self.temp_hint=label(temps,'دمای قطعات فقط در صورت دسترسی به سنسور واقعی نمایش داده می‌شود.',7,C['amber'],bg='#091f36',anchor='e')
        self.temp_hint.pack(fill='x',pady=(4,0))

        # Bottom information band: stages, latest results, alerts.
        lower=tk.Frame(center,bg=C['bg']); lower.pack(fill='both',expand=True)
        stages=panel(lower,C['panel'],10,8); stages.pack(side='left',fill='both',expand=True,padx=(0,4))
        logs=panel(lower,C['panel'],10,8); logs.pack(side='left',fill='both',expand=True,padx=4)
        alerts=panel(lower,C['panel'],10,8); alerts.pack(side='left',fill='both',expand=True,padx=(4,0))
        section(stages,'مراحل بررسی','TEST FLOW')
        self.stage_list=label(stages,'○  مشخصات سیستم     ○  CPU     ○  RAM\n○  GPU     ○  دیسک     ○  گزارش نهایی',8,C['muted'],bg=C['panel'],justify='right',anchor='e')
        self.stage_list.pack(fill='both',expand=True)
        section(logs,'نتایج و گزارش لحظه‌ای','LIVE RESULTS')
        table=tk.Frame(logs,bg=C['panel']); table.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(table,columns=('test','status','detail'),show='headings',height=4)
        for col,title,width in [('test','آزمون',62),('status','وضعیت',76),('detail','جزئیات',180)]:
            self.tree.heading(col,text=title); self.tree.column(col,width=width,anchor='e' if col=='detail' else 'center')
        self.tree.pack(fill='both',expand=True)
        self.tree.tag_configure('ok',foreground=C['green'])
        self.tree.tag_configure('warn',foreground=C['amber'])
        self.tree.tag_configure('bad',foreground=C['red'])
        section(alerts,'هشدارها و نکات مهم','ALERT CENTER')
        self.alert_status=label(alerts,'سامانه آماده است. هشدارهای واقعی پس از بررسی در این بخش نمایش داده می‌شوند.',8,C['muted'],bg=C['panel'],justify='right',anchor='ne',wraplength=230)
        self.alert_status.pack(fill='both',expand=True)

        footer=tk.Frame(self.root,bg=C['header'],height=42,
                        highlightbackground=C['edge'],highlightthickness=1)
        footer.pack(side='bottom',fill='x'); footer.pack_propagate(False)
        credits=tk.Frame(footer,bg=C['header'])
        credits.pack(side='right',padx=16,pady=3)
        label(credits,'طراحی و برنامه‌نویسی: جواد مختاری',8,C['white'],True,bg=C['header']).pack(anchor='e')
        label(credits,'حامی طرح: مدیریت بیمارستان، جناب آقای حسن‌زاده',7,C['muted'],False,bg=C['header']).pack(anchor='e')
        label(footer,'JAVAD PC DOCTOR  •  HARDWARE HEALTH MONITOR',7,C['cyan'],True,bg=C['header']).pack(side='left',padx=16)

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
                if getattr(self, 'stress_running', False):
                    self.stress_temp_max[key] = max(hottest, self.stress_temp_max.get(key, hottest))
                    self.stress_temp_samples[key] = self.stress_temp_samples.get(key, 0) + 1
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
        if not hasattr(self, 'metric_values'):
            return
        try:
            cpu_pct = float(psutil.cpu_percent(interval=None)) if psutil else None
        except Exception:
            cpu_pct = None
        try:
            vm = psutil.virtual_memory() if psutil else None
            ram_pct = float(vm.percent) if vm else None
            ram_text = f"{vm.used / (1024**3):.1f} / {vm.total / (1024**3):.1f} GB" if vm else self.info.get('RAM total','نامشخص')
        except Exception:
            ram_pct, ram_text = None, self.info.get('RAM total','نامشخص')
        try:
            system_drive = os.environ.get('SystemDrive')
            du = psutil.disk_usage(system_drive + os.sep) if (psutil and system_drive) else None
            disk_pct = float(du.percent) if du else None
            disk_text = f"درایو سیستم: {du.used / (1024**3):.0f} / {du.total / (1024**3):.0f} GB" if du else 'ظرفیت درایو سیستم قابل خواندن نیست'
        except Exception:
            disk_pct, disk_text = None, 'ظرفیت درایو سیستم قابل خواندن نیست'
        cpu_model = (self.info.get('CPU details','شناسایی نشد').split(' | ')[0] or 'مدل CPU شناسایی نشد')
        ram_modules = self.info.get('RAM modules','شناسایی نشد')
        ram_count = len(ram_modules.split(' ؛ ')) if ram_modules != 'شناسایی نشد' else 0
        gpu_details = self.info.get('GPU details','شناسایی نشد')
        gpu_model = gpu_details.split(' | ')[0] if gpu_details != 'شناسایی نشد' else 'مدل GPU شناسایی نشد'
        disk_models = self.info.get('Physical disks','شناسایی نشد')
        disk_model = disk_models.split(' | ')[0] if disk_models != 'شناسایی نشد' else 'مدل دیسک شناسایی نشد'
        values = {
            'CPU': (f"{cpu_pct:.0f}٪" if cpu_pct is not None else 'نامشخص',
                    cpu_model[:62]),
            'RAM': (f"{ram_pct:.0f}٪" if ram_pct is not None else 'نامشخص',
                    f"{ram_text} | ماژول‌های شناسایی‌شده: {ram_count}" if ram_count else ram_text),
            'GPU': (gpu_model[:48], 'درصد بار GPU در دسترس نیست؛ مدل به‌تنهایی سلامت را تأیید نمی‌کند'),
            'DISK': (f"{disk_pct:.0f}٪ اشغال" if disk_pct is not None else 'نامشخص',
                     f"{disk_text} | {disk_model[:45]}")
        }
        for key, (value, detail) in values.items():
            self.metric_values[key].config(text=value)
            self.metric_details[key].config(text=detail)
        # Never display an unavailable measurement as a measured zero.
        for key, pct in [('CPU',cpu_pct),('RAM',ram_pct),('DISK',disk_pct)]:
            bar = self.metric_bars[key]
            if pct is None:
                bar.pack_forget()
            else:
                if not bar.winfo_manager():
                    bar.pack(fill='x',ipady=2)
                bar['value'] = pct
        # GPU utilization is unavailable on this data source; hide its bar.
        self.metric_bars['GPU'].pack_forget()
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
        self._show_test_notice()
        threading.Thread(target=self._full_worker,daemon=True).start()
    def start_one(self, what):
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running: return
        self.tree.delete(*self.tree.get_children()); self.results=[]; self.running=True; self.status.config(text='● در حال تست...',fg=YELLOW)
        self._show_test_notice()
        threading.Thread(target=self._one_worker,args=(what,),daemon=True).start()

    def _show_test_notice(self):
        # Large glass-style floating notice, centered near the top edge.
        # It stays non-modal and is positioned below the application header.
        self._hide_test_notice()
        win = tk.Toplevel(self.root)
        self.test_notice = win
        win.overrideredirect(True)
        win.configure(bg='#071a2b')
        win.attributes('-topmost', True)
        try:
            win.attributes('-alpha', 0.93)
        except Exception:
            pass

        width, height = 440, 122
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = max(self.root.winfo_width(), width)
        x = root_x + (root_w - width) // 2
        y = root_y + 82
        win.geometry(f'{width}x{height}+{x}+{y}')

        shell = tk.Frame(win, bg='#071a2b', highlightbackground='#19d9f2',
                         highlightthickness=1, bd=0)
        shell.pack(fill='both', expand=True)
        accent = tk.Frame(shell, bg='#19d9f2', height=4)
        accent.pack(side='top', fill='x')
        body = tk.Frame(shell, bg='#0b2944')
        body.pack(fill='both', expand=True)

        canvas = tk.Canvas(body, width=88, height=88, bg='#0b2944',
                           highlightthickness=0, bd=0)
        canvas.pack(side='left', padx=(17, 13), pady=9)
        canvas.create_oval(9, 9, 79, 79, outline='#174765', width=5)
        arc = canvas.create_arc(9, 9, 79, 79, start=25, extent=105,
                                style='arc', outline='#19d9f2', width=5)
        canvas.create_oval(35, 35, 53, 53, fill='#19d9f2', outline='')
        canvas.create_oval(39, 39, 49, 49, fill='#0b2944', outline='')

        text_frame = tk.Frame(body, bg='#0b2944')
        text_frame.pack(side='right', fill='both', expand=True,
                        padx=(0, 20), pady=(15, 10))
        top_line = tk.Frame(text_frame, bg='#0b2944')
        top_line.pack(fill='x')
        tk.Label(top_line, text='●  LIVE MONITORING', bg='#0b2944',
                 fg='#19d9f2', font=('Segoe UI', 8, 'bold'),
                 anchor='e').pack(side='right')
        tk.Label(text_frame, text='لطفاً به سیستم دست نزنید',
                 bg='#0b2944', fg='#ffffff',
                 font=('Segoe UI', 15, 'bold'), anchor='e').pack(fill='x', pady=(5, 2))
        tk.Label(text_frame, text='بررسی در حال اجراست؛ تا پایان عملیات صبر کنید',
                 bg='#0b2944', fg='#a9c9dc',
                 font=('Segoe UI', 9), anchor='e').pack(fill='x')
        self._notice_angle = 25

        def animate():
            if not getattr(self, 'test_notice', None) or not self.test_notice.winfo_exists():
                return
            self._notice_angle = (self._notice_angle + 14) % 360
            canvas.itemconfigure(arc, start=self._notice_angle)
            self._notice_after = self.root.after(65, animate)

        animate()

    def _hide_test_notice(self):
        try:
            if getattr(self,'_notice_after',None):
                self.root.after_cancel(self._notice_after)
                self._notice_after = None
            if getattr(self,'test_notice',None) and self.test_notice.winfo_exists():
                self.test_notice.destroy()
        except Exception:
            pass
        self.test_notice = None
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
        self.running=False
        self._hide_test_notice()
        self.status.config(text='● بررسی تمام شد',fg=GREEN); self.summary.config(text='بررسی به پایان رسید. نتیجه‌ها را در جدول ببینید و گزارش را ذخیره کنید.')
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
        health=disk_health()
        status='گزارش ویندوز دریافت شد' if ('Healthy' in health or 'خوب' in health) else 'نیازمند بررسی'
        if 'قابل دریافت نبود' in health:
            status='نیازمند بررسی'
        return status, health.replace('\n',' | ') + ' | این گزارش معادل آزمون کامل SMART یا اسکن سطح دیسک نیست.'
    def test_gpu(self):
        gpu=self.info.get('GPU details','شناسایی نشد')
        if not gpu or gpu == 'شناسایی نشد':
            return 'نیازمند بررسی', 'مدل GPU از ویندوز قابل شناسایی نبود؛ سلامت کارت گرافیک قابل نتیجه‌گیری نیست.'
        return 'اطلاعات خوانده شد', f"مشخصات گزارش‌شده GPU: {gpu}. این نسخه تست عملکرد سه‌بعدی یا VRAM اجرا نمی‌کند؛ شناسایی مدل به معنی تأیید سلامت نیست."
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
        """Run a genuine, bounded 30-minute diagnostic; never label unavailable tests as passed."""
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running or self.stress_running:
            return
        if run_30_minute_diagnostic is None:
            messagebox.showerror('موتور آزمون', 'موتور آزمون واقعی بارگذاری نشد؛ هیچ آزمونی آغاز نمی‌شود.')
            return
        self.tree.delete(*self.tree.get_children()); self.results=[]
        self.stress_temp_max = {}; self.stress_temp_samples = {}
        self.stress_cancel.clear(); self.running=True; self.stress_running=True
        self.status.config(text='● آزمون واقعی در حال اجرا',fg=YELLOW)
        self.summary.config(text='آزمون‌های واقعی و محدود CPU، RAM، GPU و دیسک؛ نتایج فقط از اندازه‌گیری ثبت می‌شوند.')
        self.stress_start_btn.config(state='disabled'); self.stress_stop_btn.config(state='normal')
        self._show_test_notice()
        self._stress_ui(0,'آماده‌سازی آزمون واقعی',1800,'آزمون‌های واقعی؛ شدت سبک و محدود')
        threading.Thread(target=self._stress_worker,daemon=True).start()

    def stop_stress_test(self):
        if self.stress_running:
            self.stress_cancel.set()
            self.stress_stage.config(text='درخواست توقف آزمون...')
            self.stress_stop_btn.config(state='disabled')

    def _stress_worker(self):
        outcome = 'آزمون متوقف شد'
        try:
            def progress(percent, stage, remaining, detail):
                self._ui(self._stress_ui, percent, stage, remaining, detail)
            def result_callback(result):
                self._ui(self.add_result, result.get('test','آزمون'),
                         result.get('status','نامشخص'), result.get('detail',''))
            report = run_30_minute_diagnostic(self.stress_cancel, progress, result_callback)
            elapsed = report.get('elapsed_seconds', 0)
            if report.get('completed'):
                outcome = 'آزمون واقعی ۳۰ دقیقه‌ای تکمیل شد'
            elif report.get('cancelled'):
                outcome = 'آزمون توسط کاربر متوقف شد'
            else:
                outcome = 'آزمون پیش از تکمیل پایان یافت'
            self._ui(self._stress_finish, outcome, report)
        except Exception as exc:
            self._ui(self._stress_finish, f'آزمون با خطا متوقف شد: {exc}', None)

    def _stress_finish(self, outcome, report):
        if self.stress_temp_max:
            temp_parts = [f"{key}: بیشینه {value:.1f}°C ({self.stress_temp_samples.get(key, 0)} نمونه)" for key, value in self.stress_temp_max.items()]
            self.add_result('دما', 'ثبت شد', ' | '.join(temp_parts))
        else:
            self.add_result('دما', 'نامشخص', 'در طول آزمون هیچ سنسور دمای قابل خواندنی در دسترس نبود؛ دما حدس زده نشده است.')
        self.running=False; self.stress_running=False
        self._hide_test_notice()
        self.stress_start_btn.config(state='normal'); self.stress_stop_btn.config(state='disabled')
        self.status.config(text='● '+outcome,fg=GREEN if report and report.get('completed') else YELLOW)
        if report and report.get('completed'):
            self._stress_ui(100, 'آزمون ۳۰ دقیقه‌ای تکمیل شد', 0,
                            'آزمون‌های واقعی انجام شدند؛ محدودیت هر قطعه در گزارش نهایی مشخص است.')
            self.summary.config(text='آزمون واقعی پایان یافت؛ برای مشاهده جزئیات، گزارش را ذخیره کنید.')
        else:
            seconds = report.get('elapsed_seconds', 0) if report else 0
            self.summary.config(text=f'آزمون کامل نشد؛ مدت اجرا {seconds//60:02d}:{seconds%60:02d}. نتایج ناقص به‌عنوان قبولی ثبت نمی‌شوند.')

    def show_sensors(self):
        try:
            sensors = read_hardware_temperatures()
            lines = []
            labels = {'CPU':'پردازنده','GPU':'گرافیک','DISK':'دیسک','BOARD':'مادربرد'}
            for key, title in labels.items():
                values = sensors.get(key, [])
                if values:
                    lines.append(f'[{title}]')
                    lines.extend(f'{name}: {value:.1f}°C' for name, value in values)
                else:
                    lines.append(f'[{title}] شناسایی نشد')
            lines.append('')
            lines.append('منبع دما: ' + (sensors.get('SOURCE') or 'در دسترس نیست'))
            lines.append('')
            lines.append('=== مشخصات شناسایی‌شده سخت‌افزار ===')
            inventory = get_system_info()
            for key, title in [('CPU details','پردازنده'),('RAM modules','ماژول‌های RAM'),
                               ('GPU details','کارت گرافیک'),('Physical disks','دیسک‌های فیزیکی'),
                               ('Windows disk health','گزارش سلامت دیسک ویندوز'),
                               ('Motherboard details','مادربرد'),('BIOS','BIOS')]:
                lines.append(f'{title}: {inventory.get(key) or "شناسایی نشد"}')
            lines.append('')
            lines.append('دمای ناموجود حدس زده نمی‌شود. گزارش سلامت دیسک، گزارش ویندوز است و جایگزین آزمون کامل SMART نیست. سرعت فن در این نسخه هنوز از همه سخت‌افزارها قابل خواندن نیست.')
            messagebox.showinfo('مشخصات سخت‌افزار و سنسورها', '\n'.join(lines))
        except Exception as exc:
            messagebox.showinfo('دما و سنسورها', f'خواندن اطلاعات ناموفق بود: {exc}\nدمای ناموجود حدس زده نمی‌شود.')
    def save_report(self):
        self.refresh_info()
        stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); name=f"Report_{self.info.get('Computer','PC')}_{stamp}.txt".replace(' ','_')
        path=REPORTS/name
        lines=[APP_NAME+f' v{VERSION}', 'زمان: '+datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               '\n=== ارزیابی اولیه تکنسین (یادداشت داخلی) ===', self.technician_assessment,
               'این ارزیابی دستی است و نتیجه اندازه‌گیری قطعات محسوب نمی‌شود.',
               '\n=== مشخصات سیستم ===']
        lines += [f'{k}: {v}' for k,v in self.info.items()]
        lines += ['\n=== نتایج آزمون‌ها ===']
        lines += [f"{r['test']} | {r['status']} | {r['detail']} | {r['time']}" for r in self.results]
        lines += ['\n=== روش اجرا و محدودیت‌ها ===',
                  'آزمون ۳۰ دقیقه‌ای نسخه ۱.۷ شامل بار محاسباتی سبک روی یک رشته CPU، آزمون محدود نوشتن/بازخوانی حافظه تخصیص‌یافته RAM، آزمون Direct3D بومی WinSAT در صورت پشتیبانی، آزمون نوشتن/بازخوانی فایل موقت دیسک با تطبیق SHA-256 و پایش دوره‌ای است.',
                  'فایل آزمون دیسک موقت است و پس از آزمون حذف می‌شود؛ فایل‌های شخصی کاربر تغییر داده نمی‌شوند.',
                  'آزمون RAM درون ویندوز محدود به حافظه تخصیص‌یافته است و جایگزین MemTest86 بوتیبل برای بررسی کل حافظه نیست.',
                  'اگر WinSAT/Direct3D یا سنسوری در دسترس نباشد، آن بخش «قابل اجرا نیست/نامشخص» گزارش می‌شود؛ عدم دسترسی به معنی قبولی یا خرابی نیست.',
                  'آزمون دیسک با فایل موقت، جایگزین گزارش SMART سازنده یا اسکن کامل سطح دیسک نیست.',
                  'آزمون سبک CPU و تست‌های محدود این برنامه گواهی سلامت قطعه یا جایگزین ابزار تخصصی سازنده نیستند.',
                  'ارزیابی اولیه تکنسین جدا از داده‌های اندازه‌گیری‌شده نگهداری می‌شود و نتایج را تغییر نمی‌دهد.']
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
    art.create_text(42,230,text='حامی طرح: مدیریت بیمارستان، جناب آقای حسن‌زاده',anchor='w',
                    fill='#8faec8',font=('Segoe UI',9))
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
