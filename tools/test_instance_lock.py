"""单实例锁的回归测试（纯逻辑，不开窗）。

测的是「屏幕上会不会出现两个弹窗」这件事的根因：
- 旧进程还活着 → 必须认出来（并且默认替换掉）；
- 旧进程已经死了 / pid 被系统复用 → 不能误杀别人；
- `--multi` → 允许并存（调试渲染用）；
- 退出时只清自己的锁。

跑：python -X utf8 tools/test_instance_lock.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import instance_lock as il  # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, what):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {what}")
    else:
        FAIL += 1
        print(f"  FAIL {what}")


def fresh_lock():
    fd, path = tempfile.mkstemp(suffix=".lock")
    os.close(fd)
    os.remove(path)
    return path


def main() -> int:
    print("[1] 没有锁文件 → 认为是空的")
    p = fresh_lock()
    ok(il.owner(p) is None, "无锁文件时 owner() 是 None")
    ok(il.read_lock(p) is None, "读不到的锁当 None")

    print("[2] 写进去 + 进程活着 + 创建时间对得上 → 认它")
    info = il.claim(p, argv=["streamer.py"], pid=4242, ctime=123456)
    ok(info["pid"] == 4242, "claim 写了自己的 pid")
    got = il.owner(p, alive=lambda pid: pid == 4242, ctime=lambda pid: 123456)
    ok(got is not None and got["pid"] == 4242, "活着的旧实例被认出来")
    ok(got["argv"] == ["streamer.py"], "argv 一起留着（方便排查是谁开的）")

    print("[3] 进程死了 → 当没有，不误杀")
    ok(il.owner(p, alive=lambda pid: False, ctime=lambda pid: 123456) is None,
       "死进程不算主人")

    print("[4] pid 被系统复用（创建时间不同）→ 不认")
    ok(il.owner(p, alive=lambda pid: True, ctime=lambda pid: 999) is None,
       "创建时间对不上就不认（防止杀掉复用同号的无关进程）")

    print("[5] 没有 ctime 字段的老锁 → 只按存活判断（向后兼容）")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"pid": 4242}, fh)
    ok(il.owner(p, alive=lambda pid: True, ctime=lambda pid: 999) is not None,
       "老格式锁仍能识别在跑的实例")

    print("[6] take_over：默认替换（杀旧 + 写自己）")
    il.claim(p, argv=["old"], pid=4242, ctime=123456)
    killed = []
    out = il.take_over(p, argv=["new"], alive=lambda pid: True,
                       ctime=lambda pid: 123456,
                       kill=killed.append, note=lambda *_: None)
    ok(killed == [4242], "默认把旧实例杀掉")
    ok(out["pid"] == os.getpid(), "锁换成了自己")

    print("[7] take_over：--multi 不杀，但仍写自己")
    il.claim(p, argv=["old"], pid=4242, ctime=123456)
    killed = []
    notes = []
    out = il.take_over(p, argv=["new"], multi=True, alive=lambda pid: True,
                       ctime=lambda pid: 123456,
                       kill=killed.append, note=notes.append)
    ok(killed == [], "--multi 下不杀旧实例")
    ok(any("4242" in str(n) for n in notes), "会提示旧实例还在跑")
    ok(out["pid"] == os.getpid(), "锁仍然换成了自己")

    print("[8] 旧实例已死 → 不杀，直接接管")
    il.claim(p, argv=["old"], pid=4242, ctime=123456)
    killed = []
    il.take_over(p, argv=["new"], alive=lambda pid: False,
                 ctime=lambda pid: 123456, kill=killed.append, note=lambda *_: None)
    ok(killed == [], "死进程不需要杀")

    print("[9] release 只清自己的锁")
    il.claim(p, pid=os.getpid(), ctime=1)
    il.release(p)
    ok(il.read_lock(p) is None, "自己的锁被清掉")
    il.claim(p, pid=4242, ctime=1)
    il.release(p, pid=os.getpid())
    ok(il.read_lock(p) is not None, "别人的锁不许动")

    print("[10] 真进程探针（自己一定活着、pid=1 一定不是我们）")
    ok(il.pid_alive(os.getpid()) is True, "pid_alive(自己) 为真")
    ok(il.pid_alive(0) is False, "pid_alive(0) 为假")
    ok(il.proc_ctime(os.getpid()) is not None, "能读到自己的创建时间")

    for f in (p,):
        try:
            os.remove(f)
        except OSError:
            pass
    print(f"\n{'ALL OK' if not FAIL else 'FAILED'}  pass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
