# -*- coding: utf-8 -*-
"""Real, bounded Windows hardware diagnostics for Javad PC Doctor.

All tests are non-destructive to user data. Disk I/O uses a temporary file that is
removed in a finally block. Results describe measurements, not blanket health claims.
"""
import os
import time
import tempfile
import hashlib
import subprocess
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

TOTAL_SECONDS = 30 * 60
STAGES = [
    ("پردازنده CPU", 8 * 60),
    ("حافظه RAM", 6 * 60),
    ("کارت گرافیک GPU", 4 * 60),
    ("دیسک SSD / HDD", 5 * 60),
    ("پایش نهایی و پایداری", 7 * 60),
]


def _cancelled(cancel):
    return cancel.is_set()


def _wait_stage(seconds, cancel, progress, stage_name, detail, started, total_before):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _cancelled(cancel):
            return False
        remaining_stage = max(0, int(deadline - time.monotonic()))
        elapsed_stage = seconds - remaining_stage
        total_elapsed = total_before + elapsed_stage
        remaining_total = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
        percent = min(100, int(total_elapsed * 100 / TOTAL_SECONDS))
        progress(percent, stage_name, remaining_total, detail)
        cancel.wait(min(1.0, max(0.05, deadline - time.monotonic())))
    return not _cancelled(cancel)


def _cpu_light_test(seconds, cancel, progress, started, total_before):
    """One logical CPU, approximately 15% duty cycle; no multi-core torture load."""
    stage = "پردازنده CPU"
    begin = time.monotonic()
    cycles = 0
    while time.monotonic() - begin < seconds:
        if _cancelled(cancel):
            break
        busy_until = time.monotonic() + 0.15
        value = cycles & 0xFFFF
        while time.monotonic() < busy_until:
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
        cycles += 1
        cancel.wait(0.85)
        elapsed = min(seconds, time.monotonic() - begin)
        remaining = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
        progress(min(100, int((total_before + elapsed) * 100 / TOTAL_SECONDS)),
                 stage, remaining,
                 f"بار سبک واقعی روی یک رشته پردازشی؛ چرخه‌های محاسباتی ثبت‌شده: {cycles:,}")
    return {
        "test": "CPU",
        "status": "اجرا شد" if not _cancelled(cancel) else "متوقف شد",
        "detail": f"آزمون محاسباتی سبک روی یک رشته؛ چرخه‌های تکمیل‌شده: {cycles:,}. این آزمون جایگزین آزمون سنگین پایداری یا تشخیص قطعی خرابی CPU نیست."
    }


def _ram_test(seconds, cancel, progress, started, total_before):
    """Allocate a bounded buffer, write a known pattern, and verify it repeatedly."""
    stage = "حافظه RAM"
    if psutil is None:
        return {"test": "RAM", "status": "قابل اجرا نیست", "detail": "psutil نصب نیست؛ آزمون نوشتن/خواندن RAM اجرا نشد."}
    try:
        available = int(psutil.virtual_memory().available)
        size = min(64 * 1024 * 1024, max(8 * 1024 * 1024, available // 20))
        if available < 32 * 1024 * 1024:
            return {"test": "RAM", "status": "قابل اجرا نیست", "detail": "حافظه آزاد برای اجرای ایمن آزمون کافی نیست."}
        chunk = bytes((i * 17 + 31) & 255 for i in range(1024 * 1024))
        buffer = bytearray(size)
        begin = time.monotonic()
        passes = 0
        while time.monotonic() - begin < seconds and not _cancelled(cancel):
            for offset in range(0, size, len(chunk)):
                end = min(size, offset + len(chunk))
                buffer[offset:end] = chunk[:end-offset]
            mismatch = None
            for offset in range(0, size, len(chunk)):
                end = min(size, offset + len(chunk))
                if buffer[offset:end] != chunk[:end-offset]:
                    mismatch = offset
                    break
            passes += 1
            if mismatch is not None:
                return {"test": "RAM", "status": "خطا", "detail": f"عدم تطابق داده در آفست {mismatch} بایت؛ آزمون متوقف شد."}
            elapsed = min(seconds, time.monotonic() - begin)
            remaining = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
            progress(min(100, int((total_before + elapsed) * 100 / TOTAL_SECONDS)),
                     stage, remaining,
                     f"نوشتن و بازخوانی واقعی {size / (1024**2):.0f} مگابایت؛ دور موفق: {passes}")
            cancel.wait(0.2)
        del buffer
        return {"test": "RAM", "status": "اجرا شد" if not _cancelled(cancel) else "متوقف شد",
                "detail": f"حافظه آزمایشی تخصیص‌یافته: {size / (1024**2):.0f} MB؛ دورهای نوشتن/بازخوانی و مقایسه موفق: {passes}. این آزمون محدود ویندوزی جایگزین MemTest86 بوتیبل و آزمون کل RAM نیست."}
    except Exception as exc:
        return {"test": "RAM", "status": "نیازمند بررسی", "detail": f"اجرای آزمون RAM ناموفق بود: {exc}"}


def _gpu_test(seconds, cancel, progress, started, total_before):
    """Try Microsoft's native Direct3D assessment; otherwise report unsupported honestly."""
    stage = "کارت گرافیک GPU"
    detail = "پایش مرحله GPU؛ آزمون بار سه‌بعدی در حال بررسی قابلیت ویندوز است."
    result = "قابل اجرا نیست"
    test_detail = "ابزار WinSAT/Direct3D در این ویندوز در دسترس نیست؛ تست سه‌بعدی GPU اجرا نشد. فقط شناسایی و پایش حسگرها ممکن است."
    if os.name == "nt":
        try:
            win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "winsat.exe"
            exe = str(win) if win.exists() else "winsat"
            p = subprocess.run([exe, "d3d", "-time", "60"], capture_output=True,
                               text=True, timeout=75,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if p.returncode == 0:
                result = "اجرا شد"
                output = (p.stdout or p.stderr or "").strip().replace("\r", " ")
                test_detail = "آزمون Direct3D بومی WinSAT با موفقیت اجرا شد. خروجی: " + (output[-1200:] if output else "کد خروجی صفر")
            else:
                test_detail = f"WinSAT در دسترس بود اما آزمون Direct3D با کد {p.returncode} پایان یافت؛ سلامت GPU تأیید نمی‌شود."
        except subprocess.TimeoutExpired:
            test_detail = "آزمون Direct3D از مهلت ۷۵ ثانیه عبور کرد و متوقف شد؛ نتیجه GPU نامشخص است."
        except Exception as exc:
            test_detail = f"اجرای آزمون Direct3D ممکن نشد: {exc}"
    begin = time.monotonic()
    while time.monotonic() - begin < seconds and not _cancelled(cancel):
        elapsed = min(seconds, time.monotonic() - begin)
        remaining = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
        progress(min(100, int((total_before + elapsed) * 100 / TOTAL_SECONDS)),
                 stage, remaining, detail)
        cancel.wait(1.0)
    return {"test": "GPU", "status": result, "detail": test_detail}


def _disk_test(seconds, cancel, progress, started, total_before):
    """Read/write/verify a small temporary test file; never touches user documents."""
    stage = "دیسک SSD / HDD"
    if psutil is None:
        return {"test": "DISK", "status": "قابل اجرا نیست", "detail": "psutil نصب نیست؛ آزمون I/O اجرا نشد."}
    path = None
    try:
        system_drive = os.environ.get("SystemDrive", "")
        anchor = system_drive + os.sep if system_drive else tempfile.gettempdir()
        usage = psutil.disk_usage(anchor)
        if usage.free < 128 * 1024 * 1024:
            return {"test": "DISK", "status": "قابل اجرا نیست", "detail": "فضای آزاد کافی برای فایل آزمایشی موقت وجود ندارد؛ هیچ فایلی ساخته نشد."}
        fd, path = tempfile.mkstemp(prefix="JPD_DiskTest_", suffix=".tmp")
        os.close(fd)
        block = bytes((i * 29 + 7) & 255 for i in range(1024 * 1024))
        begin = time.monotonic()
        rounds = 0
        while time.monotonic() - begin < seconds and not _cancelled(cancel):
            digest_expected = hashlib.sha256()
            with open(path, "wb") as f:
                for _ in range(16):
                    f.write(block)
                    digest_expected.update(block)
                f.flush()
                os.fsync(f.fileno())
            digest_actual = hashlib.sha256()
            with open(path, "rb") as f:
                while True:
                    data = f.read(1024 * 1024)
                    if not data:
                        break
                    digest_actual.update(data)
            if digest_actual.digest() != digest_expected.digest():
                return {"test": "DISK", "status": "خطا", "detail": "مقایسه checksum داده نوشته‌شده و بازخوانی‌شده یکسان نبود."}
            rounds += 1
            elapsed = min(seconds, time.monotonic() - begin)
            remaining = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
            progress(min(100, int((total_before + elapsed) * 100 / TOTAL_SECONDS)),
                     stage, remaining,
                     f"نوشتن، همگام‌سازی و بازخوانی فایل موقت ۱۶ مگابایتی؛ دور موفق: {rounds}")
            cancel.wait(0.5)
        return {"test": "DISK", "status": "اجرا شد" if not _cancelled(cancel) else "متوقف شد",
                "detail": f"دورهای نوشتن/بازخوانی و تطبیق SHA-256 موفق: {rounds}؛ فایل آزمایشی موقت پس از آزمون حذف شد. این آزمون جایگزین SMART کامل یا اسکن سطح کل دیسک نیست."}
    except Exception as exc:
        return {"test": "DISK", "status": "نیازمند بررسی", "detail": f"آزمون دیسک ناموفق بود: {exc}"}
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


def _monitor_stage(seconds, cancel, progress, started, total_before):
    stage = "پایش نهایی و پایداری"
    samples = 0
    cpu_values = []
    ram_values = []
    begin = time.monotonic()
    while time.monotonic() - begin < seconds and not _cancelled(cancel):
        cpu = ram = None
        try:
            if psutil:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
        except Exception:
            pass
        if cpu is not None:
            cpu_values.append(float(cpu))
        if ram is not None:
            ram_values.append(float(ram))
        samples += 1
        elapsed = min(seconds, time.monotonic() - begin)
        remaining = max(0, TOTAL_SECONDS - int(time.monotonic() - started))
        detail = f"نمونه‌های واقعی پایش: {samples}؛ CPU و RAM فقط در صورت دسترسی سیستم ثبت می‌شوند."
        progress(min(100, int((total_before + elapsed) * 100 / TOTAL_SECONDS)),
                 stage, remaining, detail)
        cancel.wait(1.0)
    if cpu_values and ram_values:
        detail = (f"تعداد نمونه: {samples}؛ میانگین مصرف CPU: {sum(cpu_values)/len(cpu_values):.1f}٪؛ "
                  f"میانگین مصرف RAM: {sum(ram_values)/len(ram_values):.1f}٪.")
    else:
        detail = f"تعداد نمونه‌های پایش: {samples}؛ بخشی از داده‌های CPU/RAM در دسترس نبود."
    return {"test": "پایش", "status": "ثبت شد", "detail": detail}


def run_30_minute_diagnostic(cancel, progress, result_callback):
    """Run real bounded tests and live monitoring for up to 30 minutes."""
    started = time.monotonic()
    results = []
    elapsed_before = 0
    runners = [_cpu_light_test, _ram_test, _gpu_test, _disk_test, _monitor_stage]
    for (name, duration), runner in zip(STAGES, runners):
        if _cancelled(cancel):
            break
        progress(int(elapsed_before * 100 / TOTAL_SECONDS), name,
                 TOTAL_SECONDS - elapsed_before, "شروع آزمون واقعی مرحله")
        if runner is _cpu_light_test:
            result = runner(duration, cancel, progress, started, elapsed_before)
        elif runner is _ram_test:
            result = runner(duration, cancel, progress, started, elapsed_before)
        elif runner is _gpu_test:
            result = runner(duration, cancel, progress, started, elapsed_before)
        elif runner is _disk_test:
            result = runner(duration, cancel, progress, started, elapsed_before)
        else:
            result = runner(duration, cancel, progress, started, elapsed_before)
        results.append(result)
        result_callback(result)
        if _cancelled(cancel):
            break
        elapsed_before += duration
    actual = int(time.monotonic() - started)
    completed = not _cancelled(cancel) and actual >= TOTAL_SECONDS - 2
    return {"completed": completed, "cancelled": _cancelled(cancel),
            "elapsed_seconds": actual, "results": results}
