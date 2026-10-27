"""抓取桌宠窗口渲染内容（PrintWindow）用于验证。"""
import ctypes
import ctypes.wintypes as wt
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
target_pid = None

# 找到 AemeathAgent.Desktop 进程
import subprocess
try:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process AemeathAgent.Desktop | Select-Object -First 1).Id"],
        capture_output=True, text=True, timeout=20)
    target_pid = int(out.stdout.strip())
except Exception as e:
    print("get pid fail:", e)

if not target_pid:
    raise SystemExit("no pet process")

found = []


def enum_cb(hwnd, lparam):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == target_pid:
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w >= 400 and h >= 350:  # 主桌宠窗
            found.append((hwnd, w, h, r))
    return True


user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
print("candidate windows:", [(h, w, hgt) for h, w, hgt, _ in found])

for hwnd, w, h, r in found:
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, 0)
    gdi32.SelectObject(hdc_mem, old)

    # 从 GDI 位图读像素
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    path = rf"C:\Users\Kirano星岚\AppData\Local\Doubao\User Data\ClipboardTemp\petwin_{w}x{h}.png"
    img.save(path)
    print("saved", path, img.size, "printwindow ok=", ok)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)
