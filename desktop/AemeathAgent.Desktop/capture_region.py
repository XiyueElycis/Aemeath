"""直接抓取真实屏幕指定区域（验证桌宠窗口屏幕显示）。"""
import ctypes
import ctypes.wintypes as wt
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

left, top, right, bottom = 1480, 660, 2070, 1180  # 覆盖桌宠窗口区域
w, h = right - left, bottom - top

hdc_screen = user32.GetDC(0)
hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
old = gdi32.SelectObject(hdc_mem, bmp)
gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, left, top, 0x00CC0020)
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
bmi.biWidth = w
bmi.biHeight = -h
bmi.biPlanes = 1
bmi.biBitCount = 32
buf = ctypes.create_string_buffer(w * h * 4)
gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)

img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
img.save(r"C:\Users\Kirano星岚\AppData\Local\Doubao\User Data\ClipboardTemp\screen_region.png")
print("saved", img.size)

gdi32.DeleteObject(bmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(0, hdc_screen)
