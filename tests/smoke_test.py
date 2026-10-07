# -*- coding: utf-8 -*-
"""
生命周期冒烟测试：验证「开始 -> 执行 -> 完成 -> 按钮/状态复位」完整状态流转，
以及智能条件（颜色判断）各分支（就绪命中/就绪超时/完成命中/完成超时继续）。
- 不产生真实点击（click_at 被替换为 no-op）
- 屏幕像素读取被替换为可控假数据（get_pixel_color 被 monkeypatch）
- 窗口自动隐藏（root.withdraw），不打扰用户
用法：python tests/smoke_test.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_clicker as ac

import tkinter as tk

# ---------------- 可控假像素 / 假点击 ----------------
FAKE_PIXEL = [0, 0, 0]
DONE_COLOR = [0, 255, 0]
RESPOND_AFTER_CLICK = False  # 点击后模拟页面响应（像素变为完成色）
CLICK_COUNT = []


def fake_pixel(x, y):
    return tuple(FAKE_PIXEL)


def fake_click(x, y, hold_ms=15):
    CLICK_COUNT.append(1)
    if RESPOND_AFTER_CLICK:
        FAKE_PIXEL[:] = DONE_COLOR


ac.get_pixel_color = fake_pixel
ac.click_at = fake_click


def run_case(root, app, label, scheduled=False):
    CLICK_COUNT.clear()
    app.tasks[:] = [{"x": 100, "y": 100, "count": 1, "interval_ms": 0,
                     "ready": None, "done": None}]
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


def run_cond_case(root, app, label, ready, done, policy, expect_clicks,
                  wait_secs=5):
    CLICK_COUNT.clear()
    app.tasks[:] = [{"x": 100, "y": 100, "count": 1, "interval_ms": 0,
                     "ready": ready, "done": done}]
    app._refresh_tree()
    app.rounds_var.set("1")
    app.mode_var.set("immediate")
    app.use_ntp_var.set(False)
    app.timeout_policy_var.set(policy)
    app._start_clicking()
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)
    ok = (app.running is False
          and str(app.start_btn.cget("state")) == "normal"
          and app.status_var.get() == "已停止"
          and len(CLICK_COUNT) == expect_clicks)
    print(f"[{label}] running={app.running} "
          f"start_btn={app.start_btn.cget('state')} "
          f"status={app.status_var.get()!r} clicks={len(CLICK_COUNT)} "
          f"-> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    root = tk.Tk()
    root.withdraw()
    app = ac.AutoClickerApp(root)
    ok1 = run_case(root, app, "immediate-1")
    ok2 = run_case(root, app, "immediate-2(repeat)")
    ok3 = run_case(root, app, "scheduled-3s", scheduled=True)

    red = {"x": 100, "y": 100, "color": [255, 0, 0], "tol": 30, "timeout": 2}
    green = {"x": 100, "y": 100, "color": [0, 255, 0], "tol": 30, "timeout": 1}

    FAKE_PIXEL[:] = [255, 0, 0]
    ok4 = run_cond_case(root, app, "cond-ready-hit", red, None, "stop", 1)

    FAKE_PIXEL[:] = [0, 0, 255]
    ok5 = run_cond_case(root, app, "cond-ready-timeout-stop", red, None, "stop", 0)

    FAKE_PIXEL[:] = [0, 0, 0]
    RESPOND_AFTER_CLICK = True
    ok6 = run_cond_case(root, app, "cond-done-hit", None, green, "stop", 1)

    FAKE_PIXEL[:] = [0, 0, 0]
    RESPOND_AFTER_CLICK = False
    ok7 = run_cond_case(root, app, "cond-done-timeout-continue", None, green,
                        "continue", 1)

    root.destroy()
    results = [ok1, ok2, ok3, ok4, ok5, ok6, ok7]
    print("ALL PASS" if all(results) else f"FAILURES: {[i+1 for i, r in enumerate(results) if not r]}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
