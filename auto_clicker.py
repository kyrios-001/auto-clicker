# -*- coding: utf-8 -*-
"""
AutoClicker —— 定时定点自动点击器（Windows / 纯标准库 / 零第三方依赖）
=====================================================================

功能特性
--------
1. 任务列表：可配置多个点击目标（屏幕坐标 X/Y）、每次点击次数、点击间隔；
2. 两种启动方式：立即开始 / 指定时刻定时开始（HH:MM:SS，精确到秒）；
3. 可选 NTP 网络时间校准：抢课等场景下修正本机时钟偏差；
4. 轮次控制：整组任务重复 N 轮（0 = 无限循环，配合“次数=0”可无限点击）；
5. 一键捕获鼠标位置（3 秒倒计时内把鼠标移到目标即可）；
6. 全局热键：F6 开始/停止切换，F7 紧急停止；
7. 配置自动保存 / 加载（auto_clicker_config.json，与脚本同目录）；
8. 实时日志、运行倒计时、总点击计数；
9. 可选窗口置顶，方便边看日志边操作。

运行要求
--------
* Windows 10/11
* Python 3.8+（本脚本不依赖任何第三方库）

用法
----
    python auto_clicker.py          # 打开图形界面
    python auto_clicker.py --check  # 自检（验证 Win32 接口，不弹窗）

免责声明
--------
请仅在个人设备上、用于自动化个人重复操作。请遵守学校教务系统、
各网站平台的使用规则与服务条款；因滥用导致的账号风险由使用者自行承担。
"""

import ctypes
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

APP_NAME = "AutoClicker 定时定点自动点击器"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "auto_clicker_config.json")

# ---------------------------------------------------------------------------
# Win32 常量与结构
# ---------------------------------------------------------------------------

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F6 = 0x75  # F6：开始 / 停止切换
VK_F7 = 0x76  # F7：紧急停止


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_size_t),
        ("time", ctypes.c_uint),
        ("pt", POINT),
    ]


user32 = ctypes.windll.user32
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = ctypes.c_int
user32.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                               ctypes.c_uint, ctypes.c_void_p]
user32.mouse_event.restype = None
user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                               ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = ctypes.c_int
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_void_p
user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.UnregisterHotKey.restype = ctypes.c_int


def get_cursor_pos():
    """读取当前鼠标指针的屏幕坐标。"""
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def click_at(x, y, hold_ms=15):
    """
    把鼠标移动到 (x, y) 并完成一次左键点击。
    通过 SetCursorPos + mouse_event(LDOWN/UP) 实现，不依赖第三方库。
    """
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.005)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(max(0.001, hold_ms / 1000.0))
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def ntp_time(hosts=("ntp.aliyun.com", "cn.pool.ntp.org", "ntp.tencent.com"),
             timeout=1.2):
    """
    通过标准 NTP 协议获取网络时间（Unix 时间戳，秒）。
    依次尝试多个服务器，全部失败返回 None。
    """
    for host in hosts:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(b"\x1b" + 47 * b"\x00", (host, 123))
                data, _ = sock.recvfrom(48)
            if len(data) < 44:
                continue
            # NTP 传输时间戳位于第 40~43 字节（32 位秒），基准为 1900-01-01
            secs = struct.unpack("!I", data[40:44])[0]
            return secs - 2208988800
        except OSError:
            continue
    return None


def run_hotkey_listener(stop_event, on_f6, on_f7, log_cb):
    """
    全局热键监听线程：注册 F6 / F7 后泵送消息循环。
    RegisterHotKey 绑定当前线程的消息队列，GetMessageW 阻塞等待热键消息。
    """
    ok_f6 = user32.RegisterHotKey(None, 1, MOD_NOREPEAT, VK_F6) != 0
    ok_f7 = user32.RegisterHotKey(None, 2, MOD_NOREPEAT, VK_F7) != 0
    if not ok_f6 and not ok_f7:
        log_cb("警告：全局热键注册失败（可能被其他程序占用），请改用界面按钮")
        return
    msg = MSG()
    while not stop_event.is_set():
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result <= 0:
            break
        if msg.message == WM_HOTKEY:
            if msg.wParam == 1:
                on_f6()
            elif msg.wParam == 2:
                on_f7()
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnregisterHotKey(None, 1)
    user32.UnregisterHotKey(None, 2)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

class AutoClickerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(640, 480)
        self.tasks = []            # [{"x","y","count","interval_ms"}, ...]
        self.running = False
        self.stop_event = threading.Event()
        self._events = queue.Queue()
        self._clicks = 0
        self._capture_pending = False

        self._build_ui()
        self._load_config()
        self._refresh_tree()
        self._start_hotkeys()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------

    def _build_ui(self):
        root = self.root
        pad = {"padx": 6, "pady": 4}

        # 任务列表
        list_frame = ttk.LabelFrame(root, text="任务列表（坐标 / 次数 / 间隔）")
        list_frame.pack(fill="both", expand=True, **pad)
        cols = ("id", "x", "y", "count", "interval")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=8)
        heads = {"id": "编号", "x": "X 坐标", "y": "Y 坐标",
                 "count": "次数(0=无限)", "interval": "间隔(毫秒)"}
        widths = {"id": 60, "x": 90, "y": 90, "count": 110, "interval": 120}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center")
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 添加 / 编辑任务
        edit_frame = ttk.LabelFrame(root, text="添加 / 编辑任务")
        edit_frame.pack(fill="x", **pad)
        ttk.Button(edit_frame, text="捕获鼠标位置（3秒）",
                   command=self._capture_pos).grid(row=0, column=0, rowspan=2,
                                                   padx=4, pady=4)
        self.x_var = tk.StringVar(value="100")
        self.y_var = tk.StringVar(value="100")
        self.count_var = tk.StringVar(value="1")
        self.interval_var = tk.StringVar(value="50")
        ttk.Label(edit_frame, text="X:").grid(row=0, column=1, sticky="e")
        ttk.Entry(edit_frame, textvariable=self.x_var, width=8).grid(row=0, column=2)
        ttk.Label(edit_frame, text="Y:").grid(row=0, column=3, sticky="e")
        ttk.Entry(edit_frame, textvariable=self.y_var, width=8).grid(row=0, column=4)
        ttk.Label(edit_frame, text="次数:").grid(row=1, column=1, sticky="e")
        ttk.Entry(edit_frame, textvariable=self.count_var, width=8).grid(row=1, column=2)
        ttk.Label(edit_frame, text="间隔ms:").grid(row=1, column=3, sticky="e")
        ttk.Entry(edit_frame, textvariable=self.interval_var, width=8).grid(row=1, column=4)
        ttk.Button(edit_frame, text="添加任务", command=self._add_task).grid(
            row=0, column=5, rowspan=2, padx=4, pady=4)
        ttk.Button(edit_frame, text="删除选中", command=self._delete_selected).grid(
            row=0, column=6, rowspan=2, padx=4, pady=4)
        ttk.Button(edit_frame, text="清空任务", command=self._clear_tasks).grid(
            row=0, column=7, rowspan=2, padx=4, pady=4)

        # 运行设置
        run_frame = ttk.LabelFrame(root, text="运行设置")
        run_frame.pack(fill="x", **pad)
        ttk.Label(run_frame, text="轮次(0=无限)").grid(row=0, column=0, sticky="e", padx=4)
        self.rounds_var = tk.StringVar(value="1")
        ttk.Entry(run_frame, textvariable=self.rounds_var, width=5).grid(
            row=0, column=1, padx=4)
        self.mode_var = tk.StringVar(value="immediate")
        ttk.Radiobutton(run_frame, text="立即开始", value="immediate",
                        variable=self.mode_var).grid(row=0, column=2, padx=4)
        ttk.Radiobutton(run_frame, text="定时开始", value="scheduled",
                        variable=self.mode_var).grid(row=0, column=3, padx=4)
        self.schedule_var = tk.StringVar(value="12:00:00")
        ttk.Entry(run_frame, textvariable=self.schedule_var, width=8).grid(
            row=0, column=4, padx=4)
        self.use_ntp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(run_frame, text="NTP校准", variable=self.use_ntp_var).grid(
            row=0, column=5, padx=4)
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(run_frame, text="窗口置顶", variable=self.topmost_var,
                        command=self._toggle_topmost).grid(row=0, column=6, padx=4)

        self.start_btn = ttk.Button(run_frame, text="▶ 开始（F6）",
                                    command=self._start_clicking)
        self.start_btn.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=4)
        self.stop_btn = ttk.Button(run_frame, text="■ 停止（F7）",
                                   command=self._stop_clicking, state="disabled")
        self.stop_btn.grid(row=1, column=3, columnspan=4, sticky="ew", padx=4, pady=4)

        # 状态栏 + 日志
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status_var, anchor="w",
                  foreground="#0066cc").pack(fill="x", **pad)
        log_frame = ttk.LabelFrame(root, text="运行日志")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # ---------------- 事件轮询（跨线程安全） ----------------

    def _log(self, text):
        self._events.put(("log", text))

    def _append_log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_events(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._worker_finished()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _worker_finished(self):
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if self._clicks:
            self._log(f"本轮共点击 {self._clicks} 次")
            self._clicks = 0
        self.status_var.set("已停止")

    # ---------------- 任务操作 ----------------

    def _capture_pos(self):
        if self._capture_pending:
            return
        self._capture_pending = True
        self._log("请在 3 秒内把鼠标移动到目标位置…")
        self.root.after(3000, self._do_capture)

    def _do_capture(self):
        self._capture_pending = False
        x, y = get_cursor_pos()
        self.x_var.set(str(x))
        self.y_var.set(str(y))
        self._log(f"已捕获鼠标位置：({x}, {y})")

    def _add_task(self):
        try:
            x = int(self.x_var.get().strip())
            y = int(self.y_var.get().strip())
            count = int(self.count_var.get().strip())
            iv = int(self.interval_var.get().strip())
        except ValueError:
            messagebox.showwarning("提示", "X / Y / 次数 / 间隔 必须是整数（次数 0 表示无限）")
            return
        if iv < 0 or count < 0:
            messagebox.showwarning("提示", "次数与间隔不能为负数")
            return
        self.tasks.append({"x": x, "y": y, "count": count, "interval_ms": iv})
        self._refresh_tree()
        self._save_config()

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中要删除的任务")
            return
        for item in reversed(sel):
            try:
                idx = int(item)
            except ValueError:
                continue
            if 0 <= idx < len(self.tasks):
                self.tasks.pop(idx)
        self._refresh_tree()
        self._save_config()

    def _clear_tasks(self):
        if self.tasks and messagebox.askyesno("确认", "确定清空全部任务？"):
            self.tasks.clear()
            self._refresh_tree()
            self._save_config()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self.tasks):
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, t["x"], t["y"], t["count"], t["interval_ms"]))

    # ---------------- 运行控制 ----------------

    def _start_clicking(self):
        if self.running:
            return
        if not self.tasks:
            messagebox.showwarning("提示", "请先添加至少一个点击任务")
            return
        try:
            rounds = int(self.rounds_var.get().strip())
        except ValueError:
            rounds = -1
        if rounds < 0:
            messagebox.showwarning("提示", "轮次需为非负整数（0=无限）")
            return
        wait = 0.0
        if self.mode_var.get() == "scheduled":
            wait = self._compute_wait()
            if wait is None:
                return
            if wait > 7200:
                if not messagebox.askyesno(
                        "确认", f"距定时时间还有约 {wait / 3600:.1f} 小时，确认开始等待？"):
                    return
        tasks = [dict(t) for t in self.tasks]
        self._clicks = 0
        self.running = True
        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(
            target=self._worker,
            args=(tasks, rounds, wait, bool(self.use_ntp_var.get())),
            daemon=True,
        ).start()

    def _compute_wait(self):
        """解析 HH:MM:SS，返回距该时刻的秒数（今天已过则顺延到明天）。"""
        text = self.schedule_var.get().strip()
        try:
            parts = text.split(":")
            if len(parts) != 3:
                raise ValueError
            h, m, s = (int(p) for p in parts)
            if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "定时时间格式应为 HH:MM:SS，例如 12:30:00")
            return None
        now = time.localtime()
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                             h, m, s, 0, 0, -1))
        if target <= time.time():
            target += 86400
        return target - time.time()

    def _worker(self, tasks, rounds, wait, use_ntp):
        try:
            if use_ntp and wait > 0:
                self._log("正在校准网络时间（NTP）…")
                net_secs = ntp_time()
                if net_secs is None:
                    self._log("警告：NTP 校准失败，将按本机时钟执行")
                else:
                    offset = net_secs - time.time()
                    wait = wait - offset
                    self._log(f"NTP 校准完成：网络与本机时钟偏差 {offset:+.1f}s（已折算）")
            if wait > 0:
                self._log(f"已就绪，将在 {wait:.1f} 秒后开始执行（F7 可取消）")
                deadline = time.time() + wait
                while True:
                    remain = deadline - time.time()
                    if remain <= 0:
                        break
                    self._events.put(("status", f"距离开始还有 {remain:.1f} 秒"))
                    if self.stop_event.wait(min(0.5, max(0.05, remain))):
                        self._events.put(("status", "已取消"))
                        self._log("已取消")
                        return
            self._events.put(("status", "正在执行…"))
            self._log("开始执行点击任务（F7 停止）")
            round_no = 0
            while True:
                round_no += 1
                if self.stop_event.is_set():
                    break
                for t in tasks:
                    if self.stop_event.is_set():
                        break
                    x, y, count, iv_ms = t["x"], t["y"], t["count"], t["interval_ms"]
                    iv = max(0.0, iv_ms / 1000.0)
                    done = 0
                    while count == 0 or done < count:
                        if self.stop_event.is_set():
                            break
                        click_at(x, y)
                        self._clicks += 1
                        done += 1
                        if (count == 0 or done < count) and iv > 0:
                            self.stop_event.wait(iv)
                if rounds != 0 and round_no >= rounds:
                    break
            if self.stop_event.is_set():
                self._log("任务已被手动停止")
            else:
                self._log("任务完成")
        finally:
            self._events.put(("done",))

    def _stop_clicking(self):
        if self.running:
            self.stop_event.set()
            self._log("正在停止…（等待当前点击结束）")

    def _toggle(self):
        if self.running:
            self._stop_clicking()
        else:
            self._start_clicking()

    # ---------------- 全局热键 ----------------

    def _start_hotkeys(self):
        self._hk_stop = threading.Event()
        threading.Thread(
            target=run_hotkey_listener,
            args=(self._hk_stop, self._on_f6, self._on_f7, self._log),
            daemon=True,
        ).start()

    def _on_f6(self):
        # 热键线程不能直接操作 tkinter，投递到主线程执行
        self.root.after(0, self._toggle)

    def _on_f7(self):
        self.root.after(0, self._stop_clicking)

    # ---------------- 其他 ----------------

    def _toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _save_config(self):
        data = {
            "tasks": self.tasks,
            "mode": self.mode_var.get(),
            "schedule": self.schedule_var.get(),
            "rounds": self.rounds_var.get(),
            "use_ntp": bool(self.use_ntp_var.get()),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        tasks = data.get("tasks")
        if isinstance(tasks, list):
            clean = []
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                if not all(k in t for k in ("x", "y", "count", "interval_ms")):
                    continue
                try:
                    clean.append({"x": int(t["x"]), "y": int(t["y"]),
                                  "count": int(t["count"]),
                                  "interval_ms": int(t["interval_ms"])})
                except (TypeError, ValueError):
                    continue
            self.tasks = clean
        if data.get("mode") in ("immediate", "scheduled"):
            self.mode_var.set(data["mode"])
        if data.get("schedule"):
            self.schedule_var.set(str(data["schedule"]))
        if "rounds" in data:
            self.rounds_var.set(str(data["rounds"]))
        if "use_ntp" in data:
            self.use_ntp_var.set(bool(data["use_ntp"]))

    def _on_close(self):
        self._save_config()
        self.root.destroy()


# ---------------------------------------------------------------------------
# 自检模式
# ---------------------------------------------------------------------------

def run_self_check():
    print(f"Python : {sys.version.split()[0]}")
    print(f"tkinter: {tk.TkVersion}")
    x, y = get_cursor_pos()
    print(f"GetCursorPos OK -> ({x}, {y})")
    user32.SetCursorPos(x, y)  # 原地重置，不移动鼠标
    print("SetCursorPos OK")
    print("mouse_event / NTP 接口已加载")
    print("自检通过")


def main():
    if "--check" in sys.argv:
        run_self_check()
        return
    try:
        root = tk.Tk()
        AutoClickerApp(root)
        root.mainloop()
    except Exception as exc:  # 启动失败也要给出可见错误，而不是静默退出
        try:
            messagebox.showerror("启动失败", f"{type(exc).__name__}: {exc}")
        except Exception:
            print(f"启动失败: {exc}")
            input("按回车键退出…")


if __name__ == "__main__":
    main()
