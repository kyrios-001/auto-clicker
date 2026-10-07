# -*- coding: utf-8 -*-
"""
生命周期冒烟测试：验证「开始 -> 执行 -> 完成 -> 按钮/状态复位」完整状态流转。
- 不产生真实点击（click_at 被替换为 no-op）
- 窗口自动隐藏（root.withdraw），不打扰用户
用法：python tests/smoke_test.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_clicker as ac

ac.click_at = lambda x, y, hold_ms=15: None  # 阻止真实点击

import tkinter as tk


def run_case(root, app, label, scheduled=False):
    app.tasks[:] = [{"x": 100, "y": 100, "count": 1, "interval_ms": 0}]
    app._refresh_tree()
    app.rounds_var.set("1")
    app.use_ntp_var.set(False)
    if scheduled:
        app.mode_var.set("scheduled")
        target = time.localtime(time.time() + 3)
        app.schedule_var.set(time.strftime("%H:%M:%S", target))
    else:
        app.mode_var.set("immediate")
    app._start_clicking()
    deadline = time.time() + (8 if scheduled else 3)
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)
    ok = (app.running is False
          and str(app.start_btn.cget("state")) == "normal"
          and app.status_var.get() == "已停止")
    print(f"[{label}] running={app.running} "
          f"start_btn={app.start_btn.cget('state')} "
          f"status={app.status_var.get()!r} -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    root = tk.Tk()
    root.withdraw()
    app = ac.AutoClickerApp(root)
    ok1 = run_case(root, app, "immediate-1")
    ok2 = run_case(root, app, "immediate-2(repeat)")
    ok3 = run_case(root, app, "scheduled-3s", scheduled=True)
    root.destroy()
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
