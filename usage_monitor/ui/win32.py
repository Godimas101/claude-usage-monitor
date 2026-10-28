"""Typed ctypes bindings for the Win32 calls behind the v2 UI, plus the constants they use.

Every function carries explicit argtypes and restype. Handles are pointer-sized (wintypes
handles are c_void_p), LRESULT is c_ssize_t, WPARAM and LPARAM come from wintypes, so nothing is
truncated on 64-bit Python. The libraries are private WinDLL instances: declaring signatures
here never touches ``ctypes.windll``, which pywebview and the v1 modules share with default
conventions.

All of them are loaded with use_last_error, so after a failed call ``ctypes.get_last_error()``
holds the Win32 error. Call ``ctypes.set_last_error(0)`` first when a call can fail without
setting one. Windows only; nothing here runs on another platform.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
HRESULT = ctypes.c_long
VOID_P = ctypes.c_void_p
POINTER = ctypes.POINTER

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, POINTER(wintypes.RECT), wintypes.LPARAM
)

# ── Window styles, messages, hit-test codes ───────────────────────────────────

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_CLIPSIBLINGS = 0x04000000
WS_CAPTION = 0x00C00000
WS_MAXIMIZE = 0x01000000

WS_EX_NOPARENTNOTIFY = 0x00000004
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

GWL_STYLE = -16
GWL_EXSTYLE = -20
GCL_STYLE = -26
CS_DBLCLKS = 0x0008

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_ENDSESSION = 0x0016
WM_SETTINGCHANGE = 0x001A
WM_MOUSEACTIVATE = 0x0021
WM_CONTEXTMENU = 0x007B
WM_DISPLAYCHANGE = 0x007E
WM_NCDESTROY = 0x0082
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCRBUTTONDOWN = 0x00A4
WM_NCRBUTTONUP = 0x00A5
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_CAPTURECHANGED = 0x0215
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WM_WTSSESSION_CHANGE = 0x02B1
WM_NCMOUSEHOVER = 0x02A0
WM_MOUSEHOVER = 0x02A1
WM_NCMOUSELEAVE = 0x02A2
WM_MOUSELEAVE = 0x02A3
WM_DPICHANGED = 0x02E0
WM_USER = 0x0400
WM_APP = 0x8000

# Private messages (WM_APP range) understood by the controller window. WM_APP_SETTINGS tells the
# UI thread the app's settings changed (UIHost.notify_settings_changed). WM_APP_OPEN_DASHBOARD is
# what a second launch posts to the running instance. WM_APP_TRAY is the tray icon's callback.
WM_APP_POST = WM_APP + 1
WM_APP_SETTINGS = WM_APP + 2
WM_APP_OPEN_DASHBOARD = WM_APP + 3
WM_APP_TRAY = WM_APP + 4

HTCLIENT = 1
HTCAPTION = 2
MA_NOACTIVATE = 3

TME_HOVER = 0x00000001
TME_LEAVE = 0x00000002
TME_NONCLIENT = 0x00000010
HOVER_DEFAULT = 0xFFFFFFFF

PM_REMOVE = 0x0001

USER_TIMER_MINIMUM = 0x0000000A
USER_TIMER_MAXIMUM = 0x7FFFFFFF

SPI_SETWORKAREA = 0x002F
SPI_GETSCREENSAVERRUNNING = 0x0072

WTS_SESSION_LOCK = 7
WTS_SESSION_UNLOCK = 8
NOTIFY_FOR_THIS_SESSION = 0
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTSSessionInfoEx = 25
WTS_SESSIONSTATE_LOCK = 0  # Windows 8 and later; Windows 7 had lock and unlock the other way round

ERROR_ACCESS_DENIED = 5
ERROR_CLASS_ALREADY_EXISTS = 1410

# Job objects: a process in a job with this limit is terminated when the last handle to the job closes.
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

IDC_ARROW = 32512
IDI_APPLICATION = 32512

# ── Layered windows, bitmaps, icons ───────────────────────────────────────────

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
DI_NORMAL = 0x0003
GR_GDIOBJECTS = 0
GR_USEROBJECTS = 1

# ── DPI, monitors, shell ──────────────────────────────────────────────────────

DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTOPRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 0x00000001

SM_CXSMICON = 49
SM_CYSMICON = 50

ABM_GETSTATE = 0x00000004
ABM_GETTASKBARPOS = 0x00000005
ABS_AUTOHIDE = 0x00000001
ABE_LEFT = 0
ABE_TOP = 1
ABE_RIGHT = 2
ABE_BOTTOM = 3

QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4

DESKTOP_SWITCHDESKTOP = 0x0100
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_EXCLUDED_FROM_PEEK = 12
DWMWA_CLOAKED = 14

# ── Menus ─────────────────────────────────────────────────────────────────────

MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_DISABLED = 0x00000002
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800
MF_BYPOSITION = 0x00000400

TPM_LEFTALIGN = 0x0000
TPM_RIGHTALIGN = 0x0008
TPM_TOPALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

# ── Notification area ─────────────────────────────────────────────────────────

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80

NIIF_INFO = 0x01
NIIF_RESPECT_QUIET_TIME = 0x80

NOTIFYICON_VERSION_4 = 4
NIN_SELECT = WM_USER + 0
NIN_KEYSELECT = WM_USER + 1
NIN_BALLOONUSERCLICK = WM_USER + 5

# ── Structures ────────────────────────────────────────────────────────────────


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD), ("bmBits", wintypes.LPVOID),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32),
    ]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER), ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """144 bytes on 64-bit Windows, 112 on 32-bit."""

    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WTSINFOEX_HEAD(ctypes.Structure):
    """The start of WTSINFOEXW, up to SessionFlags: Level, then WTSINFOEX_LEVEL1_W (8-aligned)."""

    _fields_ = [
        ("Level", wintypes.DWORD), ("_pad", wintypes.DWORD), ("SessionId", wintypes.ULONG),
        ("SessionState", ctypes.c_int), ("SessionFlags", wintypes.LONG),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """The Vista+ layout (976 bytes on 64-bit), including hBalloonIcon."""

    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),  # uTimeout/uVersion union
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON),
    ]


# ── Function declarations ─────────────────────────────────────────────────────

def _bind(lib: ctypes.CDLL, name: str, restype: Any, *argtypes: Any) -> Any:
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_hwnd, _uint, _wp, _lp = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
_int, _bool, _dword = ctypes.c_int, wintypes.BOOL, wintypes.DWORD
_hdc, _rect_p, _point_p = wintypes.HDC, POINTER(wintypes.RECT), POINTER(wintypes.POINT)

# user32: windows and messages
RegisterClassExW = _bind(user32, "RegisterClassExW", wintypes.ATOM, POINTER(WNDCLASSEXW))
UnregisterClassW = _bind(user32, "UnregisterClassW", _bool, wintypes.LPCWSTR, wintypes.HINSTANCE)
CreateWindowExW = _bind(
    user32, "CreateWindowExW", _hwnd, _dword, wintypes.LPCWSTR, wintypes.LPCWSTR, _dword,
    _int, _int, _int, _int, _hwnd, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
DestroyWindow = _bind(user32, "DestroyWindow", _bool, _hwnd)
DefWindowProcW = _bind(user32, "DefWindowProcW", LRESULT, _hwnd, _uint, _wp, _lp)
GetMessageW = _bind(user32, "GetMessageW", _bool, POINTER(wintypes.MSG), _hwnd, _uint, _uint)
PeekMessageW = _bind(user32, "PeekMessageW", _bool, POINTER(wintypes.MSG), _hwnd, _uint, _uint, _uint)
TranslateMessage = _bind(user32, "TranslateMessage", _bool, POINTER(wintypes.MSG))
DispatchMessageW = _bind(user32, "DispatchMessageW", LRESULT, POINTER(wintypes.MSG))
PostMessageW = _bind(user32, "PostMessageW", _bool, _hwnd, _uint, _wp, _lp)
SendMessageW = _bind(user32, "SendMessageW", LRESULT, _hwnd, _uint, _wp, _lp)
PostQuitMessage = _bind(user32, "PostQuitMessage", None, _int)
SetTimer = _bind(user32, "SetTimer", UINT_PTR, _hwnd, UINT_PTR, _uint, VOID_P)
KillTimer = _bind(user32, "KillTimer", _bool, _hwnd, UINT_PTR)
RegisterWindowMessageW = _bind(user32, "RegisterWindowMessageW", _uint, wintypes.LPCWSTR)
FindWindowW = _bind(user32, "FindWindowW", _hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR)
FindWindowExW = _bind(user32, "FindWindowExW", _hwnd, _hwnd, _hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR)
GetClassNameW = _bind(user32, "GetClassNameW", _int, _hwnd, wintypes.LPWSTR, _int)
GetForegroundWindow = _bind(user32, "GetForegroundWindow", _hwnd)
SetForegroundWindow = _bind(user32, "SetForegroundWindow", _bool, _hwnd)
GetWindowThreadProcessId = _bind(user32, "GetWindowThreadProcessId", _dword, _hwnd, POINTER(_dword))
IsWindow = _bind(user32, "IsWindow", _bool, _hwnd)
IsWindowVisible = _bind(user32, "IsWindowVisible", _bool, _hwnd)
IsIconic = _bind(user32, "IsIconic", _bool, _hwnd)
ShowWindow = _bind(user32, "ShowWindow", _bool, _hwnd, _int)
SetWindowPos = _bind(user32, "SetWindowPos", _bool, _hwnd, _hwnd, _int, _int, _int, _int, _uint)
GetWindowRect = _bind(user32, "GetWindowRect", _bool, _hwnd, _rect_p)
GetClientRect = _bind(user32, "GetClientRect", _bool, _hwnd, _rect_p)
ClientToScreen = _bind(user32, "ClientToScreen", _bool, _hwnd, _point_p)
ScreenToClient = _bind(user32, "ScreenToClient", _bool, _hwnd, _point_p)
GetCursorPos = _bind(user32, "GetCursorPos", _bool, _point_p)
SetCapture = _bind(user32, "SetCapture", _hwnd, _hwnd)
ReleaseCapture = _bind(user32, "ReleaseCapture", _bool)
TrackMouseEvent = _bind(user32, "TrackMouseEvent", _bool, POINTER(TRACKMOUSEEVENT))
LoadCursorW = _bind(user32, "LoadCursorW", wintypes.HANDLE, wintypes.HINSTANCE, VOID_P)
LoadIconW = _bind(user32, "LoadIconW", wintypes.HICON, wintypes.HINSTANCE, VOID_P)
GetClassLongW = _bind(user32, "GetClassLongW", _dword, _hwnd, _int)
if hasattr(user32, "GetWindowLongPtrW"):
    GetWindowLongPtrW = _bind(user32, "GetWindowLongPtrW", LONG_PTR, _hwnd, _int)
    SetWindowLongPtrW = _bind(user32, "SetWindowLongPtrW", LONG_PTR, _hwnd, _int, LONG_PTR)
else:  # 32-bit Python: the Ptr variants are macros over the plain functions
    GetWindowLongPtrW = _bind(user32, "GetWindowLongW", LONG_PTR, _hwnd, _int)
    SetWindowLongPtrW = _bind(user32, "SetWindowLongW", LONG_PTR, _hwnd, _int, LONG_PTR)

UpdateLayeredWindow = _bind(
    user32, "UpdateLayeredWindow", _bool, _hwnd, _hdc, _point_p, POINTER(wintypes.SIZE), _hdc,
    _point_p, wintypes.COLORREF, POINTER(BLENDFUNCTION), _dword,
)

# user32: DPI, metrics, system parameters
SetProcessDpiAwarenessContext = _bind(user32, "SetProcessDpiAwarenessContext", _bool, wintypes.HANDLE)
GetThreadDpiAwarenessContext = _bind(user32, "GetThreadDpiAwarenessContext", wintypes.HANDLE)
AreDpiAwarenessContextsEqual = _bind(
    user32, "AreDpiAwarenessContextsEqual", _bool, wintypes.HANDLE, wintypes.HANDLE
)
GetDpiForWindow = _bind(user32, "GetDpiForWindow", _uint, _hwnd)
GetSystemMetrics = _bind(user32, "GetSystemMetrics", _int, _int)
GetSystemMetricsForDpi = _bind(user32, "GetSystemMetricsForDpi", _int, _int, _uint)
SystemParametersInfoW = _bind(
    user32, "SystemParametersInfoW", _bool, _uint, _uint, wintypes.LPVOID, _uint
)

# user32: monitors, desktops
MonitorFromPoint = _bind(user32, "MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, _dword)
MonitorFromWindow = _bind(user32, "MonitorFromWindow", wintypes.HMONITOR, _hwnd, _dword)
GetMonitorInfoW = _bind(user32, "GetMonitorInfoW", _bool, wintypes.HMONITOR, POINTER(MONITORINFOEXW))
EnumDisplayMonitors = _bind(
    user32, "EnumDisplayMonitors", _bool, _hdc, _rect_p, MONITORENUMPROC, _lp
)
OpenInputDesktop = _bind(user32, "OpenInputDesktop", wintypes.HDESK, _dword, _bool, _dword)
CloseDesktop = _bind(user32, "CloseDesktop", _bool, wintypes.HDESK)

# user32: menus
CreatePopupMenu = _bind(user32, "CreatePopupMenu", wintypes.HMENU)
AppendMenuW = _bind(user32, "AppendMenuW", _bool, wintypes.HMENU, _uint, UINT_PTR, wintypes.LPCWSTR)
DestroyMenu = _bind(user32, "DestroyMenu", _bool, wintypes.HMENU)
TrackPopupMenuEx = _bind(
    user32, "TrackPopupMenuEx", _bool, wintypes.HMENU, _uint, _int, _int, _hwnd, VOID_P
)
EndMenu = _bind(user32, "EndMenu", _bool)
GetMenuItemCount = _bind(user32, "GetMenuItemCount", _int, wintypes.HMENU)
GetMenuState = _bind(user32, "GetMenuState", _uint, wintypes.HMENU, _uint, _uint)
GetMenuStringW = _bind(
    user32, "GetMenuStringW", _int, wintypes.HMENU, _uint, wintypes.LPWSTR, _int, _uint
)

# user32: icons, device contexts, resource counts
CreateIconIndirect = _bind(user32, "CreateIconIndirect", wintypes.HICON, POINTER(ICONINFO))
DestroyIcon = _bind(user32, "DestroyIcon", _bool, wintypes.HICON)
GetIconInfo = _bind(user32, "GetIconInfo", _bool, wintypes.HICON, POINTER(ICONINFO))
DrawIconEx = _bind(
    user32, "DrawIconEx", _bool, _hdc, _int, _int, wintypes.HICON, _int, _int, _uint,
    wintypes.HBRUSH, _uint,
)
GetDC = _bind(user32, "GetDC", _hdc, _hwnd)
ReleaseDC = _bind(user32, "ReleaseDC", _int, _hwnd, _hdc)
GetGuiResources = _bind(user32, "GetGuiResources", _dword, wintypes.HANDLE, _dword)

# gdi32
CreateCompatibleDC = _bind(gdi32, "CreateCompatibleDC", _hdc, _hdc)
CreateDIBSection = _bind(
    gdi32, "CreateDIBSection", wintypes.HBITMAP, _hdc, POINTER(BITMAPINFO), _uint,
    POINTER(VOID_P), wintypes.HANDLE, _dword,
)
CreateBitmap = _bind(gdi32, "CreateBitmap", wintypes.HBITMAP, _int, _int, _uint, _uint, VOID_P)
SelectObject = _bind(gdi32, "SelectObject", wintypes.HGDIOBJ, _hdc, wintypes.HGDIOBJ)
DeleteObject = _bind(gdi32, "DeleteObject", _bool, wintypes.HGDIOBJ)
DeleteDC = _bind(gdi32, "DeleteDC", _bool, _hdc)
GetObjectW = _bind(gdi32, "GetObjectW", _int, wintypes.HANDLE, _int, VOID_P)
GetDIBits = _bind(
    gdi32, "GetDIBits", _int, _hdc, wintypes.HBITMAP, _uint, _uint, VOID_P, POINTER(BITMAPINFO), _uint
)

# kernel32
GetModuleHandleW = _bind(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
GetCurrentProcess = _bind(kernel32, "GetCurrentProcess", wintypes.HANDLE)
GetCurrentProcessId = _bind(kernel32, "GetCurrentProcessId", _dword)
CloseHandle = _bind(kernel32, "CloseHandle", _bool, wintypes.HANDLE)
CreateJobObjectW = _bind(kernel32, "CreateJobObjectW", wintypes.HANDLE, VOID_P, wintypes.LPCWSTR)
SetInformationJobObject = _bind(kernel32, "SetInformationJobObject", _bool, wintypes.HANDLE, _int, VOID_P, _dword)
AssignProcessToJobObject = _bind(kernel32, "AssignProcessToJobObject", _bool, wintypes.HANDLE, wintypes.HANDLE)

# shell32, shcore, dwmapi, wtsapi32
Shell_NotifyIconW = _bind(shell32, "Shell_NotifyIconW", _bool, _dword, POINTER(NOTIFYICONDATAW))
SHAppBarMessage = _bind(shell32, "SHAppBarMessage", UINT_PTR, _dword, POINTER(APPBARDATA))
SHQueryUserNotificationState = _bind(shell32, "SHQueryUserNotificationState", HRESULT, POINTER(_int))
GetDpiForMonitor = _bind(
    shcore, "GetDpiForMonitor", HRESULT, wintypes.HMONITOR, _int, POINTER(_uint), POINTER(_uint)
)
DwmGetWindowAttribute = _bind(
    dwmapi, "DwmGetWindowAttribute", HRESULT, _hwnd, _dword, wintypes.LPVOID, _dword
)
DwmSetWindowAttribute = _bind(
    dwmapi, "DwmSetWindowAttribute", HRESULT, _hwnd, _dword, wintypes.LPCVOID, _dword
)
WTSRegisterSessionNotification = _bind(
    wtsapi32, "WTSRegisterSessionNotification", _bool, _hwnd, _dword
)
WTSUnRegisterSessionNotification = _bind(wtsapi32, "WTSUnRegisterSessionNotification", _bool, _hwnd)
WTSQuerySessionInformationW = _bind(
    wtsapi32, "WTSQuerySessionInformationW", _bool, wintypes.HANDLE, _dword, _int,
    POINTER(VOID_P), POINTER(_dword),
)
WTSFreeMemory = _bind(wtsapi32, "WTSFreeMemory", None, VOID_P)


# ── Helpers ───────────────────────────────────────────────────────────────────

def loword(value: int) -> int:
    return value & 0xFFFF


def hiword(value: int) -> int:
    return (value >> 16) & 0xFFFF


def signed16(value: int) -> int:
    """The low 16 bits of ``value`` as a signed number (GET_X_LPARAM for negative monitors)."""
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def get_x_lparam(lparam: int) -> int:
    return signed16(lparam)


def get_y_lparam(lparam: int) -> int:
    return signed16(lparam >> 16)


def make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def last_error() -> int:
    """The Win32 error saved by the last call through one of the bindings above."""
    return ctypes.get_last_error()


def error_text(code: int | None = None) -> str:
    """"[WinError 5] Access is denied." style text for ``code`` (default: the last error)."""
    return str(ctypes.WinError(last_error() if code is None else code))


def create_kill_on_close_job() -> int:
    """A new job object that ends every process in it when its last handle closes, which also
    happens when the owning process dies however it dies. Returns the handle; the caller closes it
    with CloseHandle. Raises OSError on failure."""
    ctypes.set_last_error(0)
    job = CreateJobObjectW(None, None)
    if not job:
        raise OSError(f"CreateJobObjectW failed: {error_text()}")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        problem = error_text()
        CloseHandle(job)
        raise OSError(f"SetInformationJobObject failed: {problem}")
    return int(job)


def assign_to_job(job: int, process: int) -> None:
    """Put the process behind the handle ``process`` into ``job``. Raises OSError on failure."""
    ctypes.set_last_error(0)
    if not AssignProcessToJobObject(job, process):
        raise OSError(f"AssignProcessToJobObject failed: {error_text()}")


def create_dib_section(width: int, height: int) -> tuple[int, int]:
    """A top-down 32-bit BGRA DIB section. Returns (HBITMAP, address of its pixels).

    The caller owns the bitmap and must DeleteObject it. Raises OSError on failure.
    """
    header = BITMAPINFOHEADER(
        ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB, 0, 0, 0, 0, 0
    )
    info = BITMAPINFO(header)
    bits = ctypes.c_void_p()
    ctypes.set_last_error(0)
    hbitmap = CreateDIBSection(
        None, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    if not hbitmap or not bits.value:
        raise OSError(f"CreateDIBSection({width}x{height}) failed: {error_text()}")
    return hbitmap, bits.value
