"""PrintWindow 双标志抓取桌宠窗口。"""
import ctypes
import ctypes.wintypes as wt
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

hwnd = int("0x350b50", 16)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


r = RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
print("window", w, h)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def print_window(flag, out):
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, flag)
    gdi32.SelectObject(hdc_mem, old)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    img.save(out)
    print(out, img.size, "ok=", ok)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)


print_window(0, r"C:\Users\Kirano星岚\AppData\Local\Doubao\User Data\ClipboardTemp\pw0.png")
print_window(2, r"C:\Users\Kirano星岚\AppData\Local\Doubao\User Data\ClipboardTemp\pw2.png")
