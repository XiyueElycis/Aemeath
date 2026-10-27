"""移动桌宠窗口到指定位置并抓取该区域。"""
import ctypes
import ctypes.wintypes as wt
import sys
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

target_pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
nx, ny = 1450, 60  # 目标位置（左上角）


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
found = []


def enum_cb(hwnd, lparam):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == target_pid:
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w >= 400 and h >= 350:
            found.append((hwnd, w, h))
    return True


user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
print("windows:", found)

for hwnd, w, h in found:
    user32.SetWindowPos(hwnd, 0, nx, ny, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE|SWP_NOZORDER
    print("moved", hwnd, "to", nx, ny)

import time
time.sleep(1.5)

# 抓取目标区域
left, top = nx - 10, ny - 10
right, bottom = nx + w + 10, ny + h + 10
cw, ch = right - left, bottom - top
hdc_screen = user32.GetDC(0)
hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
bmp = gdi32.CreateCompatibleBitmap(hdc_screen, cw, ch)
old = gdi32.SelectObject(hdc_mem, bmp)
gdi32.BitBlt(hdc_mem, 0, 0, cw, ch, hdc_screen, left, top, 0x00CC0020)
gdi32.SelectObject(hdc_mem, old)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]

bmi = BITMAPINFOHEADER()
bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bmi.biWidth = cw
bmi.biHeight = -ch
bmi.biPlanes = 1
bmi.biBitCount = 32
buf = ctypes.create_string_buffer(cw * ch * 4)
gdi32.GetDIBits(hdc_mem, bmp, 0, ch, buf, ctypes.byref(bmi), 0)
img = Image.frombuffer("RGBA", (cw, ch), buf.raw, "raw", "BGRA", 0, 1)
img.save(r"C:\Users\Kirano星岚\AppData\Local\Doubao\User Data\ClipboardTemp\pet_moved.png")
print("saved", img.size)

gdi32.DeleteObject(bmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(0, hdc_screen)
