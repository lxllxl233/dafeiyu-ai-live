"""Act 地图寻路：枚举所有从当前行到 Boss 的路径并打分（A10 用）。

用法：
    python tools/mappath.py                # 打印 Top 8 路径 + 当前可行节点推荐
    python tools/mappath.py --top 15
    python tools/mappath.py --noelite      # 硬性禁止精英
加权（可改 SCORE）：
    Monster 4, Unknown 3, Shop 5, Treasure 8, RestSite 7, Elite -100, Boss 0
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:8080/state"
SCORE = {
    "Monster": 4, "Unknown": 3, "Shop": 5, "Treasure": 8,
    "RestSite": 7, "Elite": -100, "Boss": 0, "Ancient": 0,
}
LABEL = {
    "Monster": "M", "Unknown": "?", "Shop": "$", "Treasure": "T",
    "RestSite": "R", "Elite": "E", "Boss": "B", "Ancient": "A",
}


def load():
    with urllib.request.urlopen(API, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))["data"]["map"]


def main():
    top = 8
    ban_elite = False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--top" and i + 1 < len(args):
            top = int(args[i + 1])
        if a == "--noelite":
            ban_elite = True
    m = load()
    nodes = {(n["row"], n["col"]): n for n in m["nodes"]}
    rows = m["rows"]
    cur = (m["current_node"]["row"], m["current_node"]["col"])
    avail = [(a["row"], a["col"]) for a in m["available_nodes"]]

    paths = []

    def walk(path):
        r, c = path[-1]
        if r >= rows - 1 or nodes[(r, c)]["is_boss"]:
            paths.append(list(path))
            return
        for ch in nodes[(r, c)]["children"]:
            walk(path + [(ch["row"], ch["col"])])

    for a in avail:
        walk([cur, a])

    scored = []
    for p in paths:
        types = [nodes[x]["node_type"] for x in p[1:]]
        if ban_elite and "Elite" in types:
            continue
        s = sum(SCORE.get(t, 0) for t in types)
        scored.append((s, p, types))
    scored.sort(key=lambda x: -x[0])

    print(f"当前位置 {cur}  可行首步 {avail}  路径总数 {len(paths)}")
    for s, p, types in scored[:top]:
        trail = " ".join(LABEL.get(t, "?") for t in types)
        coords = " ".join(f"{r},{c}" for r, c in p[1:])
        print(f"score={s:4d} | {trail:36s} | {coords}")

    # 每个可行首步的最好分数
    best = {}
    for s, p, types in scored:
        first = p[1]
        if first not in best:
            best[first] = (s, types)
    print("\n首步推荐（按最好可达分数排序）：")
    order = sorted(best.items(), key=lambda kv: -kv[1][0])
    for idx, (first, (s, types)) in enumerate(order):
        ai = avail.index(first) if first in avail else -1
        trail = " ".join(LABEL.get(t, "?") for t in types)
        print(f"  option={ai} node={first} score={s:4d} | {trail}")


if __name__ == "__main__":
    main()
