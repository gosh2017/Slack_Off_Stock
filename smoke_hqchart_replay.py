"""
回放工具栏 JS 行为冒烟测试（临时脚本，用完即删）。

用真实 pywebview 加载真实 index.html，给页面里的 notify 包一层记录器，
然后直接调 window.applyPayload / applyReplayState 并模拟点击，断言：
  · 按钮文案 / disabled 状态
  · 上报给 Python 的事件名和参数

覆盖的正是之前漏掉的那类 bug：applyReplayState() 只改了按钮文案却没写回
REPLAY.state，点"继续"实际发出 replay_pause，被 Python 当重复暂停丢弃，
按钮就永远停在"继续"。
"""
import json
import threading
import time
import traceback

import webview

URL = "file:///" + (r"c:\other\Project\stock_monitor\assets\hqchart\index.html"
                    .replace("\\", "/"))

WINDOW = None
RESULTS = []

# 包住已有的 notify：既能记录，也不影响原本上报给 Python
WRAPPER = (
    "(function () {"
    "  var api = window.pywebview.api;"
    "  window.__EV = [];"
    "  var orig = api.notify.bind(api);"
    "  api.notify = function (ev, d) {"
    "    window.__EV.push({ event: ev, detail: d });"
    "    try { orig(ev, d); } catch (e) {}"
    "  };"
    "  return typeof api.notify;"
    "})()"
)

UI_JS = (
    "JSON.stringify({"
    "pause: document.getElementById('btn-pause').textContent,"
    "pauseDisabled: document.getElementById('btn-pause').disabled,"
    "replayDisabled: document.getElementById('btn-replay').disabled,"
    "latestDisabled: document.getElementById('btn-latest').disabled,"
    "status: document.getElementById('status').textContent"
    "})"
)

EV_JS = "JSON.stringify(window.__EV || [])"
CLEAR_EV_JS = "window.__EV = []; 'cleared'"
CLICK_JS = "document.getElementById('%s').click(); 'clicked'"


class JsApi:
    def notify(self, event, detail="null"):
        pass


def js(expr):
    try:
        return WINDOW.evaluate_js(expr)
    except Exception as exc:
        return "RAISED %r" % exc


def ok(name, actual, expected):
    good = actual == expected
    RESULTS.append(good)
    print(("  OK   " if good else "  FAIL ") + name +
          ("" if good else "   实际=%r 期望=%r" % (actual, expected)))


def run_checks():
    r = js(WRAPPER)
    print("包装 notify:", r)
    if r != "function":
        RESULTS.append(False)
        print("  FAIL  notify 包装失败，后续检查无意义")
        return False

    items = [{"date": "2024%02d%02d" % (1 + i // 28, 1 + i % 28),
              "open": 1000 + i, "high": 1010 + i, "low": 990 + i,
              "close": 1005 + i, "volume": 100000 + i, "amount": 0}
             for i in range(60)]
    base = {"code": "600519", "stockType": "stock", "name": "贵州茅台",
            "period": "daily", "periodType": "K_DAY"}

    def frame(n, state="running"):
        return dict(base, items=items[:n], replay=True, replayCount=n,
                    replayTotal=60, replayDate=items[n - 1]["date"],
                    replayState=state)

    def apply(fn, payload):
        js("window.%s(%s);" % (fn, json.dumps(payload)))

    def ui():
        return json.loads(js(UI_JS))

    def recorded():
        return json.loads(js(EV_JS))

    def click(btn):
        js(CLICK_JS % btn)

    def last(event_only=True):
        got = recorded()
        if not got:
            return None
        return got[-1]["event"] if event_only else got[-1]

    js(CLEAR_EV_JS)

    # ── 一、播放中 ──
    apply("applyPayload", frame(5))
    s = ui()
    ok("播放中 暂停按钮文案", s["pause"], "暂停")
    ok("播放中 暂停按钮可点", s["pauseDisabled"], False)
    ok("播放中 回放按钮禁用", s["replayDisabled"], True)
    ok("播放中 最新按钮可点", s["latestDisabled"], False)
    ok("播放中 状态栏带进度", "5/60" in s["status"], True)

    # ── 二、暂停 → 点"继续"必须发 replay_resume（回归点）──
    js(CLEAR_EV_JS)
    apply("applyReplayState", {"state": "paused", "count": 5, "total": 60,
                               "date": items[4]["date"]})
    ok("暂停后 按钮文案变继续", ui()["pause"], "继续")
    ok("暂停后 暂停按钮仍可点", ui()["pauseDisabled"], False)
    ok("暂停后 状态栏仍显示进度", "5/60" in ui()["status"], True)

    click("btn-pause")
    ok("点继续 上报 replay_resume", last(), "replay_resume")

    # ── 三、继续后 → 点"暂停"必须发 replay_pause ──
    js(CLEAR_EV_JS)
    apply("applyReplayState", {"state": "running", "count": 6, "total": 60,
                               "date": items[5]["date"]})
    ok("继续后 按钮文案变暂停", ui()["pause"], "暂停")
    click("btn-pause")
    ok("点暂停 上报 replay_pause", last(), "replay_pause")

    # ── 四、点"最新"结束回放 ──
    js(CLEAR_EV_JS)
    click("btn-latest")
    ok("点最新 上报 replay_stop", last(), "replay_stop")

    # ── 五、全量载荷 → 回到 idle ──
    js(CLEAR_EV_JS)
    apply("applyPayload", dict(base, items=items))
    s = ui()
    ok("回到最新 回放按钮可点", s["replayDisabled"], False)
    ok("回到最新 暂停按钮禁用", s["pauseDisabled"], True)
    ok("回到最新 最新按钮禁用", s["latestDisabled"], True)
    ok("回到最新 状态栏清空", s["status"], "")

    click("btn-pause")
    click("btn-latest")
    ok("禁用按钮点击不上报", recorded(), [])

    # ── 六、点"回放"开播，带上速度 ──
    js(CLEAR_EV_JS)
    js("var s = document.getElementById('sel-speed'); s.value = '150';")
    click("btn-replay")
    msg = last(event_only=False)
    ok("点回放 上报 replay_start", msg["event"] if msg else None, "replay_start")
    ok("点回放 带上速度参数", msg["detail"] if msg else None,
       '{"interval":150}')

    # ── 七、播完后「回放」仍可直接重播 ──
    js(CLEAR_EV_JS)
    apply("applyPayload", frame(60, "finished"))
    ok("播完后 回放按钮可点", ui()["replayDisabled"], False)
    ok("播完后 状态栏显示完成", "回放完成" in ui()["status"], True)
    click("btn-replay")
    ok("播完后点回放 上报 replay_start", last(), "replay_start")

    # ── 八、改速度 ──
    js(CLEAR_EV_JS)
    js("var s = document.getElementById('sel-speed');"
       "s.value = '1000';"
       "s.dispatchEvent(new Event('change'));")
    msg = last(event_only=False)
    ok("改速度 上报 replay_speed", msg["event"] if msg else None, "replay_speed")
    ok("改速度 参数正确", msg["detail"] if msg else None, '{"interval":1000}')

    passed = sum(1 for g in RESULTS if g)
    print("\n%d / %d 通过" % (passed, len(RESULTS)))
    return passed == len(RESULTS)


all_ok = [False]


def driver():
    time.sleep(8)
    try:
        if WINDOW is None:
            print("  FAIL  页面未加载")
            RESULTS.append(False)
        else:
            all_ok[0] = run_checks()
    except Exception:
        RESULTS.append(False)
        traceback.print_exc()
    time.sleep(1.5)
    if WINDOW is not None:
        WINDOW.destroy()


WINDOW = webview.create_window("回放工具栏检查", url=URL, js_api=JsApi(),
                               width=760, height=480)
threading.Thread(target=driver, daemon=True).start()
webview.start()
print("RESULT:", "PASS" if all_ok[0] else "FAIL")
