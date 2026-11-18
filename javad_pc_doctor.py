# -*- coding: utf-8 -*-
"""Javad PC Doctor - portable Windows hardware check utility.
Safe, non-destructive checks. Some sensor/SMART readings depend on Windows and hardware support.
"""
import os, sys, json, time, platform, socket, threading, subprocess, datetime, tempfile, hashlib
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import psutil
except Exception:
    psutil = None

APP_NAME = 'Javad PC Doctor'
VERSION = '1.5.0'
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
        self._style(); self._build(); self.refresh_info()

    def _style(self):
        s = ttk.Style(); s.theme_use('clam')
        s.configure('TProgressbar', troughcolor='#183653', background=CYAN, bordercolor='#183653', lightcolor=CYAN, darkcolor=CYAN)
        s.configure('Treeview', background=PANEL, foreground=TEXT, fieldbackground=PANEL, rowheight=30, borderwidth=0)
        s.configure('Treeview.Heading', background=PANEL2, foreground=TEXT, font=('Segoe UI', 10, 'bold'))
        s.map('Treeview', background=[('selected', '#155477')])

    def _build(self):
        header = tk.Frame(self.root, bg='#092344', height=94); header.pack(fill='x'); header.pack_propagate(False)
        tk.Label(header, text='🛡', font=('Segoe UI Emoji', 42), fg=CYAN, bg='#092344').pack(side='left', padx=(22,10))
        titles = tk.Frame(header, bg='#092344'); titles.pack(side='left', pady=16)
        tk.Label(titles, text='بیمارستان آیت‌الله روحانی', font=('Segoe UI', 17, 'bold'), fg='white', bg='#092344').pack(anchor='w')
        tk.Label(titles, text='Javad PC Doctor  |  ابزار بررسی و عیب‌یابی سخت‌افزار', font=('Segoe UI', 12, 'bold'), fg=CYAN, bg='#092344').pack(anchor='w')
        tk.Label(titles, text='ابزار بررسی و عیب‌یابی سخت‌افزار  •  Portable USB Edition', font=('Segoe UI', 10), fg=MUTED, bg='#092344').pack(anchor='w')
        self.status = tk.Label(header, text='● آماده بررسی', font=('Segoe UI', 11, 'bold'), fg=GREEN, bg='#092344'); self.status.pack(side='right', padx=25)

        scroll_host = tk.Frame(self.root, bg=BG); scroll_host.pack(fill='both', expand=True)
        view = tk.Canvas(scroll_host, bg=BG, highlightthickness=0, borderwidth=0)
        vbar = ttk.Scrollbar(scroll_host, orient='vertical', command=view.yview)
        view.configure(yscrollcommand=vbar.set)
        vbar.pack(side='right', fill='y'); view.pack(side='left', fill='both', expand=True)
        body = tk.Frame(view, bg=BG)
        body_window = view.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: view.configure(scrollregion=view.bbox('all')))
        view.bind('<Configure>', lambda e: view.itemconfigure(body_window, width=e.width))
        view.bind_all('<MouseWheel>', lambda e: view.yview_scroll(int(-1*(e.delta/120)), 'units'))
        left = tk.Frame(body, bg=BG, width=250); left.pack(side='left', fill='y', padx=(0,14)); left.pack_propagate(False)
        self._panel_title(left, 'عملیات عیب‌یابی')
        self.big_button(left, '▶  شروع تست کامل', self.start_full, GREEN)
        self.big_button(left, '◉  پایش تست ۳۰ دقیقه‌ای', self.start_stress_test, YELLOW)
        self.big_button(left, '▣  تست پردازنده', lambda: self.start_one('CPU'), CYAN)
        self.big_button(left, '▤  تست حافظه RAM', lambda: self.start_one('RAM'), CYAN)
        self.big_button(left, '▧  بررسی دیسک‌ها', lambda: self.start_one('DISK'), CYAN)
        self.big_button(left, '◈  اطلاعات گرافیک', lambda: self.start_one('GPU'), CYAN)
        self.big_button(left, '♨  دما و فن‌ها', self.show_sensors, YELLOW)
        self.big_button(left, '▤  گزارش‌ها', self.open_reports, MUTED)
        tk.Label(left, text='شدت پیش‌فرض: سبک\nپایش بدون ایجاد بار مصنوعی روی قطعات.', justify='left', bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(anchor='w', pady=18)

        center = tk.Frame(body, bg=BG); center.pack(side='left', fill='both', expand=True)
        self._panel_title(center, 'وضعیت سیستم')
        # Technician-only initial assessment. This is kept separate from measured hardware results.
        assess = tk.Frame(center, bg=PANEL, highlightbackground='#244966', highlightthickness=1)
        assess.pack(fill='x', pady=(0,10))
        assess_top = tk.Frame(assess, bg=PANEL); assess_top.pack(fill='x', padx=12, pady=(8,4))
        tk.Label(assess_top, text='ارزیابی اولیه تکنسین', bg=PANEL, fg=CYAN, font=('Segoe UI', 11, 'bold')).pack(side='right')
        tk.Label(assess_top, text='یادداشت داخلی', bg=PANEL, fg=MUTED, font=('Segoe UI', 8)).pack(side='left')
        assess_buttons = tk.Frame(assess, bg=PANEL); assess_buttons.pack(fill='x', padx=10, pady=(2,9))
        self.assess_stable_btn = tk.Button(assess_buttons, text='✓  پایدار', command=lambda: self.set_assessment('پایدار'), bg='#145c43', fg='white', activebackground='#1b805d', relief='flat', padx=18, pady=7, font=('Segoe UI',10,'bold'), cursor='hand2')
        self.assess_stable_btn.pack(side='right', padx=(0,7))
        self.assess_unstable_btn = tk.Button(assess_buttons, text='✕  ناپایدار', command=lambda: self.set_assessment('ناپایدار'), bg='#66313a', fg='white', activebackground='#8c3d49', relief='flat', padx=18, pady=7, font=('Segoe UI',10,'bold'), cursor='hand2')
        self.assess_unstable_btn.pack(side='right', padx=7)
        self.assess_value = tk.Label(assess_buttons, text='هنوز انتخاب نشده', bg=PANEL, fg=MUTED, font=('Segoe UI',9))
        self.assess_value.pack(side='left', padx=8)
        self.summary = tk.Label(center, text='برای شروع، تست کامل سیستم را انتخاب کنید.', bg=PANEL, fg=TEXT, font=('Segoe UI', 15, 'bold'), pady=24, wraplength=600)
        self.summary.pack(fill='x', pady=(0,12))
        self.progress = ttk.Progressbar(center, mode='determinate', maximum=100); self.progress.pack(fill='x', pady=(0,10))
        self.progress_label = tk.Label(center, text='پیشرفت: ۰٪', bg=BG, fg=MUTED, font=('Segoe UI', 9)); self.progress_label.pack(anchor='e')
        # Dedicated visual panel for the 30-minute low-intensity stress test.
        stress = tk.Frame(center, bg=PANEL, highlightbackground='#17618b', highlightthickness=1)
        stress.pack(fill='x', pady=(8,12))
        tk.Label(stress, text='پایش هوشمند تست ۳۰ دقیقه‌ای', bg=PANEL, fg=CYAN, font=('Segoe UI', 13, 'bold')).pack(anchor='e', padx=14, pady=(9,2))
        stress_row = tk.Frame(stress, bg=PANEL); stress_row.pack(fill='x', padx=12, pady=(2,10))
        self.stress_gauge = tk.Canvas(stress_row, width=116, height=116, bg=PANEL, highlightthickness=0)
        self.stress_gauge.pack(side='right', padx=(12,4))
        self.stress_gauge.create_oval(9,9,107,107, outline='#244966', width=10)
        self.stress_arc = self.stress_gauge.create_arc(9,9,107,107,start=90,extent=0,style='arc',outline=CYAN,width=10)
        self.stress_pct = self.stress_gauge.create_text(58,53,text='۰٪',fill=TEXT,font=('Segoe UI',18,'bold'))
        self.stress_gauge.create_text(58,76,text='پیشرفت',fill=MUTED,font=('Segoe UI',8))
        stress_info = tk.Frame(stress_row, bg=PANEL); stress_info.pack(side='right', fill='both', expand=True)
        self.stress_stage = tk.Label(stress_info, text='آماده شروع • حالت مرحله‌ای بدون فشار', bg=PANEL, fg=TEXT, font=('Segoe UI',11,'bold'), anchor='e')
        self.stress_stage.pack(fill='x', pady=(9,4))
        self.stress_time = tk.Label(stress_info, text='زمان کل: ۳۰:۰۰  |  باقی‌مانده: ۳۰:۰۰', bg=PANEL, fg=MUTED, font=('Segoe UI',9), anchor='e')
        self.stress_time.pack(fill='x', pady=2)
        self.stress_detail = tk.Label(stress_info, text='نمایش مراحل CPU • RAM • GPU • دیسک؛ بدون بار واقعی', bg=PANEL, fg=MUTED, font=('Segoe UI',9), anchor='e')
        self.stress_detail.pack(fill='x', pady=2)
        stress_actions = tk.Frame(stress_info, bg=PANEL); stress_actions.pack(fill='x', pady=(8,0))
        self.stress_start_btn = tk.Button(stress_actions, text='▶  شروع پایش ۳۰ دقیقه‌ای', command=self.start_stress_test, bg='#087c54', fg='white', activebackground='#0b9b69', relief='flat', padx=12, pady=8, font=('Segoe UI',10,'bold'), cursor='hand2')
        self.stress_start_btn.pack(side='right')
        self.stress_stop_btn = tk.Button(stress_actions, text='■ توقف', command=self.stop_stress_test, bg='#71313b', fg='white', activebackground='#943e49', relief='flat', padx=12, pady=8, font=('Segoe UI',9,'bold'), state='disabled', cursor='hand2')
        self.stress_stop_btn.pack(side='right', padx=7)
        self._panel_title(center, 'نتایج بررسی')
        cols = ('test','status','detail')
        self.tree = ttk.Treeview(center, columns=cols, show='headings', height=11)
        self.tree.heading('test', text='آزمون'); self.tree.heading('status', text='نتیجه'); self.tree.heading('detail', text='توضیحات')
        self.tree.column('test', width=145, anchor='center'); self.tree.column('status', width=105, anchor='center'); self.tree.column('detail', width=430, anchor='w')
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('ok', foreground=GREEN); self.tree.tag_configure('warn', foreground=YELLOW); self.tree.tag_configure('bad', foreground=RED)
        bottom = tk.Frame(self.root, bg='#092344', height=36); bottom.pack(fill='x', side='bottom'); bottom.pack_propagate(False)
        tk.Label(bottom, text=f'برنامه‌نویس: جواد مختاری   |   Javad PC Doctor  v{VERSION}   |   اجرای قابل‌حمل', bg='#092344', fg=MUTED, font=('Segoe UI', 9)).pack(side='left', padx=16, pady=8)
        tk.Button(bottom, text='ذخیره گزارش', command=self.save_report, bg=PANEL2, fg='white', relief='flat', padx=12).pack(side='right', padx=12, pady=4)

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

    def refresh_info(self):
        self.info = get_system_info()
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
        self.stress_gauge.itemconfigure(self.stress_arc, extent=-3.6*percent)
        self.stress_gauge.itemconfigure(self.stress_pct, text=digits+'٪')
        mins,secs=divmod(max(0,int(remaining)),60)
        self.stress_stage.config(text=stage)
        self.stress_time.config(text=f'زمان کل: ۳۰:۰۰  |  باقی‌مانده: {mins:02d}:{secs:02d}')
        if detail: self.stress_detail.config(text=detail)
        self.progress['value']=percent
        self.progress_label.config(text=f'پیشرفت تست فشار: {digits}٪  |  {stage}')

    def start_stress_test(self):
        """Run a 30-minute visual-only simulation. It deliberately creates no hardware load."""
        if self.technician_assessment == 'انتخاب نشده':
            messagebox.showwarning('ارزیابی اولیه', 'ابتدا وضعیت سیستم را در بخش ارزیابی اولیه تکنسین انتخاب کنید.')
            return
        if self.running or self.stress_running:
            return
        if not messagebox.askyesno('پایش تست ۳۰ دقیقه‌ای',
            'این حالت فقط مرحله‌ای است و دقیقاً ۳۰ دقیقه طول می‌کشد.\n'
            'هیچ پردازش سنگین، تخصیص آزمایشی RAM، تست دیسک یا بار گرافیکی اجرا نمی‌شود.\n'
            'این فرایند به‌تنهایی تأیید سلامت قطعات محسوب نمی‌شود.\n\n'
            'شروع شود؟'):
            return
        self.tree.delete(*self.tree.get_children()); self.results=[]
        self.stress_cancel.clear(); self.running=True; self.stress_running=True
        self.status.config(text='● پایش مرحله‌ای در حال اجرا',fg=YELLOW)
        self.summary.config(text='حالت مرحله‌ای فعال است؛ هیچ فشاری به قطعات وارد نمی‌شود.')
        self.stress_start_btn.config(state='disabled'); self.stress_stop_btn.config(state='normal')
        self._stress_ui(0,'آماده‌سازی مرحله‌ای',1800,'حالت پایش • بدون بار CPU / RAM / GPU / دیسک')
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
            ('نمایش مرحله پردازنده (CPU)', 6 * 60, 'نمایش گرافیکی مرحله CPU • هیچ بار پردازشی تولید نمی‌شود'),
            ('نمایش مرحله حافظه (RAM)', 6 * 60, 'نمایش مرحله RAM • هیچ حافظه‌ای برای آزمون تخصیص داده نمی‌شود'),
            ('نمایش مرحله گرافیک (GPU)', 6 * 60, 'نمایش مرحله GPU • هیچ رندر یا تست سه‌بعدی اجرا نمی‌شود'),
            ('نمایش مرحله دیسک (SSD / HDD)', 6 * 60, 'نمایش مرحله دیسک • هیچ اسکن یا عملیات خواندن/نوشتن انجام نمی‌شود'),
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
                         'پایان نمایش • هیچ تست فیزیکی انجام نشد؛ برای نتیجه واقعی از ابزار عیب‌یابی استفاده کنید')
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
    # Branded splash screen (uses the included generated banner artwork).
    root.withdraw()
    splash=tk.Toplevel(root); splash.title(APP_NAME); splash.configure(bg='#071629'); splash.overrideredirect(True)
    sw, sh = 720, 245
    x=(splash.winfo_screenwidth()-sw)//2; y=(splash.winfo_screenheight()-sh)//2
    splash.geometry(f'{sw}x{sh}+{x}+{y}')
    try:
        banner=tk.PhotoImage(file=str(ASSET_BASE / 'assets' / 'splash-banner.png'))
        # Scale down with integer subsampling for broad Tk compatibility.
        factor=max(1, banner.width()//720)
        if factor>1: banner=banner.subsample(factor, max(1, banner.height()//95))
        img=tk.Label(splash,image=banner,bg='#071629'); img.image=banner; img.pack(fill='x')
    except Exception:
        tk.Label(splash,text='Javad PC Doctor',font=('Segoe UI',25,'bold'),fg=CYAN,bg='#071629').pack(pady=40)
    tk.Label(splash,text='در حال آماده‌سازی ابزار عیب‌یابی سخت‌افزار...',font=('Segoe UI',11),fg=TEXT,bg='#071629').pack(pady=10)
    bar=ttk.Progressbar(splash,mode='indeterminate',length=620); bar.pack(pady=(0,14)); bar.start(12)
    def show_main():
        try: splash.destroy()
        except Exception: pass
        root.deiconify(); DoctorApp(root)
    root.after(1700, show_main)
    root.mainloop()
if __name__=='__main__': main()
