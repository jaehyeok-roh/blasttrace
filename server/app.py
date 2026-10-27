"""BlastTrace 시연 서버 (담당: 재혁). 노트북에서 띄우고, 폰은 같은 와이파이·핫스팟에서 /m 으로 접속한다.
실행 (저장소 맨 위 폴더에서): uvicorn server.app:app --host 0.0.0.0 --port 8000
처음 실행하면 data/bt.db에 예시 구역 24개가 채워진다. 되돌리기: python -m server.reset

AI 모듈은 함수 하나씩만 부른다 (노션 '개발 협업 가이드' 3단계):
  cv.predict.predict(image_path)        -> 후보 박스 (픽셀)
  cv.align.align(before_path, after_path) -> 3x3 변환
  llm.report.make_report(record)        -> 보고서 초안
  sensor.spectral.analyze(reflectance)  -> 분광 판정 (센서 값은 data/sensor.csv에서 읽는다)
도장 전 검사가 Close-out되면 같은 구역에 도장 후 사진(kind='post')을 올려 같은 흐름(후보·지적·조치·재검사)을 한 번 더 돈다.
DB 안에서는 박스를 0~1 비율로 저장한다. 사진 크기가 달라도 화면에 그대로 겹쳐 그리기 위해서다."""
import csv
import io
import json
import os
import socket
import sqlite3
import uuid
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps

from cv.align import align
from cv.predict import predict
from llm.report import make_report
from sensor.spectral import WAVELENGTHS, analyze
from server.quality import check_quality

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = Path(os.environ.get("BT_DATA", ROOT / "data"))
UPLOADS = DATA / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

# 구역 상태 8단계 -> 화면 색 클래스. Close-out = 도장 전 검사 종결, Release 완료 = 도장 후 검사까지 종결
STATUS = {"검사 대기": "wait", "촬영 불량": "badimg", "검토 대기": "review", "Punch Open": "punch",
          "조치 완료": "fixed", "재검사 대기": "recheck", "Close-out": "close", "Release 완료": "release"}
# 지적 상태 -> 구역 상태 이름. 구역에 지적이 여럿이면 앞쪽 상태가 우선한다
FINDING = {"open": "Punch Open", "recheck": "재검사 대기", "fixed": "조치 완료", "closed": "Close-out"}
# 구역 상태 -> 화면 위 진행 막대(①~⑦)에서 강조할 단계. 도장 후 사진이 있으면 상태와 상관없이 ⑦
ZONE_STEP = {"검사 대기": 1, "촬영 불량": 1, "검토 대기": 2, "Punch Open": 3, "조치 완료": 4, "재검사 대기": 5, "Close-out": 6,
             "Release 완료": 7}

app = FastAPI()
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
tpl = Jinja2Templates(directory=WEB / "templates")

# 블록 평면도: 가로 = 프레임(Fr.204~220), 세로 = 론지(L1~L7). 칸 하나(2프레임 x 2론지)가 검사 구역이다.
# 프레임은 선미(A.P)부터 0.8 m 간격으로 센다. 길이 약 305 m LNG선에서 B12(No.3 화물창 앞쪽)는 대략 Fr.201~228이고,
# 평면도는 그중 16프레임(12.8 m)만 그린다.
# 현장은 새 ID가 아니라 도면 위치로 부르므로, 구역 ID 옆에 항상 Fr/L 위치를 같이 보여 준다.
COLS, ROWS, FR0, L0 = 8, 3, 204, 1


def zone_block(zid):
    return list(BLOCKS)[(int(zid[2:]) - 1) // (COLS * ROWS)]


def zone_rc(zid):
    return divmod((int(zid[2:]) - 1) % (COLS * ROWS), COLS)  # 블록 안 (행, 열)


def zone_cell(zid):
    """평면도에 그릴 (행, 열). 위에서 볼 때 좌현 블록은 CL이 아래쪽이라 L1 줄이 맨 아래로 간다"""
    r, c = zone_rc(zid)
    return (ROWS - 1 - r if zone_block(zid).endswith("P") else r), c


def zone_loc(zid):
    r, c = zone_rc(zid)
    name, _, ax = SURFACES[zid[0]]
    return f"{name} · Fr.{FR0 + 2 * c}–{FR0 + 2 * c + 2} · {ax}{L0 + 2 * r}–{ax}{L0 + 2 * r + 2} ({zone_block(zid)[-1]})"


# 블록 안의 면 = 구역 ID 첫 글자. 블록은 뒤집혀 만들어지기도 해서 '위·아래'가 아니라 배 기준 부재 이름으로 부른다.
# (이름, 설명, 세로축 부재 기호). 면마다 같은 격자(8 x 3)를 쓰고, 지금 예시 기록은 내저판 윗면(Z)에만 있다
# LNG선(멤브레인 화물창)은 화물창 안쪽이 단열재로 덮이고, 도장 품질 기준(PSPC)이 정해진 곳은 바깥의 밸러스트 탱크다.
# 그래서 예시 기록(Z)은 이중저 밸러스트 탱크에 둔다
SURFACES = {"Z": ("이중저 밸러스트 탱크", "탱크 바닥 · PSPC", "L"),
            "H": ("하부 호퍼 경사판", "화물창 쪽 경사면", "HL"),
            "D": ("내저판 윗면", "화물창 쪽 · 단열재 설치면", "L"),
            "O": ("선저 외판 바깥", "배 바깥 바닥", "L")}


# 홈의 블록 분할도: LNG 운반선, 선미(왼쪽)부터 블록 07~16 (화물창 No.4~No.1, 탱크 하나 = 블록 3개, B12 = No.3 화물창 앞쪽). 데이터가 있는 블록은 선저 B12의 좌현(P)·우현(S)이다.
# 구역 ID는 호선 안에서 겹치지 않게 B12P = ?-01~24, B12S = ?-25~48 (? = 면 글자)
SHIP = "H-2231"
BLOCKS = {"B12P": "좌현", "B12S": "우현"}
SHIP_COLS = [f"{n:02d}" for n in range(7, 17)]
tpl.env.globals.update(STATUS=STATUS, FINDING=FINDING, LOC=zone_loc, BLK=zone_block, CELL=zone_cell, COLS=COLS, ROWS=ROWS,
                       FR0=FR0, L0=L0, WL=WAVELENGTHS, SHIP=SHIP, BLOCKS=BLOCKS, SHIP_COLS=SHIP_COLS, SURFACES=SURFACES,
                       # 폰 브라우저가 옛 CSS를 붙잡고 있지 않도록 수정 시각을 주소에 붙인다
                       CSS_V=lambda: int((WEB / "static" / "style.css").stat().st_mtime),
                       JS_V=lambda: int((WEB / "static" / "ship3d.js").stat().st_mtime))


# 노트북 사용자 = 지적·종결을 승인하는 검사원. Hold Point 해제 기록에 이 이름이 남는다
INSPECTOR = "품질관리자 A"
# 사진 출처. 성능은 출처를 섞지 않고 따로 센다 (멘토링: 공개 데이터 / 합성 / 시편 / 현장 실측 분리)
ORIGINS = ["현장", "시편", "공개", "합성"]
# 폰으로 새로 찍은 사진의 출처. 해커톤은 평판 시편 촬영이라 기본 시편, 현장에 쓰면 BT_ORIGIN=현장
ORIGIN = os.environ.get("BT_ORIGIN", "시편")
tpl.env.globals.update(INSPECTOR=INSPECTOR, EV=lambda: one("SELECT MAX(id) v FROM events")["v"] or 0,
                       EP=lambda: one("SELECT MAX(id) v FROM photos")["v"] or 0)


def ahash(img):
    g = np.asarray(img.convert("L").resize((8, 8), Image.BILINEAR), float)
    return (g > g.mean()).flatten()


SAMPLE_HASHES = [ahash(Image.open(p)) for p in sorted((WEB / "static" / "samples").glob("*.png"))]


def origin_of(path):
    """사진 출처. 시연용 예시 사진(가공·합성, web/static/samples)과 같은 사진이면 '합성', 아니면 ORIGIN.
    ponytail: 8x8 평균 해시라 메신저로 다시 압축·축소돼도 알아보지만, 잘라낸 사진은 못 알아본다"""
    h = ahash(Image.open(path))
    return "합성" if any((h != s).sum() <= 4 for s in SAMPLE_HASHES) else ORIGIN


# AI 후보를 반려할 때 고르는 이유. 반려 이유가 곧 학습 데이터의 '헷갈리는 경우' 라벨이 된다
REASONS = ["그림자·반사", "용접 비드", "마킹·글씨", "먼지·오염", "기타"]
# '같은 자리' 기준 (화면·보고서·PDF에 같은 문장을 쓴다). 조금만 스쳐도 같은 자리로 세지 않으려고 중심점으로 본다
SAME_SPOT = "도장 후 결함 박스의 중심이 사진 정합으로 옮긴 도장 전 위치 안에 있으면 같은 자리"
tpl.env.globals.update(REASONS=REASONS, SAME_SPOT=SAME_SPOT)


def block_or_404(bid):
    if bid not in BLOCKS:
        raise HTTPException(404, "예시 블록입니다 (데이터 없음)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones(id TEXT PRIMARY KEY, block TEXT, status TEXT, mock INT DEFAULT 0, hp1 TEXT, hp2 TEXT);
CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, zone_id TEXT, kind TEXT, src TEXT, w INT, h INT,
  glare REAL, blur REAL, ok INT, model TEXT, reviewed INT DEFAULT 0, ts TEXT, origin TEXT);
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, photo_id INT, x0 REAL, y0 REAL, x1 REAL, y1 REAL, score REAL,
  decision TEXT, reason TEXT, decided_at TEXT, source TEXT DEFAULT 'ai');
CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY, zone_id TEXT, photo_id INT, cand_id INT,
  x0 REAL, y0 REAL, x1 REAL, y1 REAL, score REAL, status TEXT, note TEXT, ts TEXT, reworks INT DEFAULT 0, closed_at TEXT);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, finding_id INT, note TEXT, src TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS rechecks(id INTEGER PRIMARY KEY, finding_id INT, photo_id INT, poly TEXT,
  matches INT, success INT, ts TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, zone_id TEXT, ts TEXT, text TEXT, mock INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS links(photo_id INTEGER PRIMARY KEY, pre_id INT, homography TEXT, matches INT, success INT);
"""


# ---------- DB 도우미 ----------

def q(sql, *a):
    with sqlite3.connect(DATA / "bt.db") as c:
        c.row_factory = sqlite3.Row
        return c.execute(sql, a).fetchall()


def one(sql, *a):
    r = q(sql, *a)
    return r[0] if r else None


def run(sql, *a):
    with sqlite3.connect(DATA / "bt.db") as c:
        return c.execute(sql, a).lastrowid


def now():
    return datetime.now().strftime("%m-%d %H:%M")


def log(zid, text, ts=None, mock=0):
    run("INSERT INTO events(zone_id, ts, text, mock) VALUES(?,?,?,?)", zid, ts or now(), text, mock)


def set_status(zid, status):
    """구역 상태를 바꾼다. Close-out = Hold Point 1(도장 전 표면처리 검사) 해제 -> 도장 착수 가능,
    Release 완료 = Hold Point 2(도장 후 검사) 해제. 둘 다 검사원 판단(재검사 종결, 지적 없음 확정)으로만 오고,
    AI는 해제하지 않는다. 처음 해제한 사람과 시각을 남긴다."""
    run("UPDATE zones SET status=? WHERE id=?", status, zid)
    for hp, name in ((1, "Close-out"), (2, "Release 완료")):
        if status == name and not one("SELECT * FROM zones WHERE id=?", zid)[f"hp{hp}"]:
            run(f"UPDATE zones SET hp{hp}=? WHERE id=?", f"{INSPECTOR} · {now()}", zid)
            log(zid, f"Hold Point {hp} 해제 · 승인 {INSPECTOR}")


def refresh(zid):
    """지적들의 상태로 구역 상태를 다시 정한다. 도장 후 검사까지 확인했으면 종결은 Release 완료다."""
    have = {r["status"] for r in q("SELECT status FROM findings WHERE zone_id=?", zid)}
    for k, name in FINDING.items():
        if k in have:
            if k == "closed" and one("SELECT 1 FROM photos WHERE zone_id=? AND kind='post' AND reviewed=1", zid):
                name = "Release 완료"
            return set_status(zid, name)


def post_phase(z):
    """도장 전 검사가 끝나 다음 촬영이 도장 후 사진인 구역인가"""
    return z["status"] == "Close-out" or bool(one("SELECT 1 FROM photos WHERE zone_id=? AND kind='post'", z["id"]))


def zone_or_404(zid):
    z = one("SELECT * FROM zones WHERE id=?", zid)
    if not z:
        raise HTTPException(404, "없는 구역")
    return z


def finding_or_404(fid):
    f = one("SELECT * FROM findings WHERE id=?", fid)
    if not f:
        raise HTTPException(404, "없는 지적")
    return f


# ---------- 사진·좌표 ----------

def fpath(src):
    """'/uploads/a.jpg' 같은 화면 주소를 디스크 경로 문자열로 바꾼다."""
    p = UPLOADS / src.removeprefix("/uploads/") if src.startswith("/uploads/") else WEB / src.lstrip("/")
    return str(p)


def save(f: UploadFile):
    """폰 사진의 회전 정보(EXIF)를 픽셀에 반영하고 긴 변 2000px로 줄여 저장한다.
    이렇게 해야 브라우저와 OpenCV가 같은 방향의 사진을 본다."""
    try:
        img = ImageOps.exif_transpose(Image.open(f.file)).convert("RGB")
    except Exception:
        raise HTTPException(400, "사진 파일이 아닙니다")
    img.thumbnail((2000, 2000))
    name = uuid.uuid4().hex[:12] + ".jpg"
    img.save(UPLOADS / name, quality=90)
    return "/uploads/" + name, img.size


def norm_box(c, w, h):
    """predict 결과(픽셀 x, y, w, h) -> 0~1 비율 (x0, y0, x1, y1)"""
    return c["x"] / w, c["y"] / h, (c["x"] + c["w"]) / w, (c["y"] + c["h"]) / h


def move_box(box, H, before_wh, after_wh):
    """조치 전 사진의 지적 박스를 정합 변환으로 재검사 사진 위 4점 다각형(0~1 비율)으로 옮긴다."""
    (bw, bh), (aw, ah) = before_wh, after_wh
    x0, y0, x1, y1 = box
    pts = np.array([[x0 * bw, x1 * bw, x1 * bw, x0 * bw], [y0 * bh, y0 * bh, y1 * bh, y1 * bh], [1, 1, 1, 1]])
    p = np.array(H) @ pts
    return [[round(p[0, i] / p[2, i] / aw, 4), round(p[1, i] / p[2, i] / ah, 4)] for i in range(4)]


def moved_pre(post, rejected=False):
    """같은 구역 도장 전 지적(rejected=True면 검사원이 반려한 AI 후보)들을 도장 후 사진 위로 옮긴다.
    반환: (정합 기록, [(도장 전 행, 4점)])"""
    link = one("SELECT * FROM links WHERE photo_id=?", post["id"])
    if not (link and link["success"]):
        return link, []
    pre = one("SELECT * FROM photos WHERE id=?", link["pre_id"])
    H = json.loads(link["homography"])
    sql = ("SELECT * FROM candidates WHERE photo_id=? AND decision='rejected' ORDER BY id" if rejected
           else "SELECT * FROM findings WHERE photo_id=? ORDER BY id")
    return link, [(f, move_box((f["x0"], f["y0"], f["x1"], f["y1"]), H, (pre["w"], pre["h"]), (post["w"], post["h"])))
                  for f in q(sql, pre["id"])]


def pre_marks(post):
    """도장 후 사진 위에 그릴 도장 전 지적 위치. 반환: (정합 기록, [(이름, 4점)])"""
    link, moved = moved_pre(post)
    return link, [(f"도장 전 I-{f['id']:02d}", pts) for f, pts in moved]


def overlaps(pts, b):
    """같은 자리인가: 박스 b(도장 후 지적)의 중심이 옮겨 온 4점 다각형(도장 전 위치) 안(경계 포함)에 있다"""
    c = ((b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2)
    return cv2.pointPolygonTest(np.float32(pts).reshape(-1, 1, 2), c, False) >= 0


def spots(post):
    """도장 후 지적마다 도장 전 같은 자리에 무엇이 있었나 = 도장 전 판단을 도장 후 결과로 채점하는 라벨.
    '재발' = 채택한 지적 자리(조치했는데 다시 나옴), '반려' = 반려한 AI 후보 자리(AI는 잡았는데 검사원이 반려),
    '기록 없음' = 도장 전에 아무 표시도 없던 자리(둘 다 놓쳤거나 도장 중에 생김). 정합 실패면 빈 목록.
    반환: [(도장 후 지적, 종류, 도장 전 지적 / 반려 후보 / None)]"""
    link, found = moved_pre(post)
    if not (link and link["success"]):
        return []
    _, rej = moved_pre(post, rejected=True)
    out = []
    for b in q("SELECT * FROM findings WHERE photo_id=? ORDER BY id", post["id"]):
        hits = [(b, "재발", a) for a, pts in found if overlaps(pts, b)] + [(b, "반려", c) for c, pts in rej if overlaps(pts, b)]
        out += hits or [(b, "기록 없음", None)]
    return out


def recur(post):
    """같은 자리 재발만. 반환: [(도장 전 지적, 도장 후 지적)]"""
    return [(a, b) for b, k, a in spots(post) if k == "재발"]


# ---------- 분광 (도장 후 지적의 도막 상태 보조 측정) ----------

SENSOR_COLS = ["location_id", "measured_at", "ref"] + [f"ch{i}" for i in range(1, 15)] + ["source"]
# sim = 센서 없이 시연값으로 측정, device = 실제 센서(ESP32 등)가 /api/sensor 로 측정값을 보낸다
SENSOR_MODE = os.environ.get("BT_SENSOR", "sim")
# 분광은 실제 센서로 "사진으론 애매한 곳"을 가르는지 시편 검증하기 전까지 데모 화면에서 숨긴다. 켜기: BT_SPECTRAL=1
SPECTRAL = os.environ.get("BT_SPECTRAL") == "1"
ARMED = {}  # device 모드: 폰에서 '분광 측정'을 누른 지적. 센서가 보내는 다음 값이 여기 붙는다 (재시작하면 비워짐)


def loc_id(f):
    """센서 CSV의 location_id. 측정할 때 이 값을 넣는다 (예: Z-03-I-01)"""
    return f"{f['zone_id']}-I-{f['id']:02d}"


def spectra(loc):
    """data/sensor.csv에서 이 위치의 측정을 시간순으로 읽는다. 반사율 = sample / 바로 앞 white."""
    path = DATA / "sensor.csv"
    if not path.exists():
        return []
    out, white = [], None
    with open(path, encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            if r["location_id"] != loc:
                continue
            ch = [float(r[f"ch{i}"]) for i in range(1, 13)]
            if r["ref"] == "white":
                white = ch
            elif white:
                refl = [s / w if w else 0.0 for s, w in zip(ch, white)]
                out.append({"at": r["measured_at"][5:16], "source": r.get("source") or "",
                            "refl": [round(x, 3) for x in refl], **analyze(refl)})
    return out


def sim_reading(name):
    """시뮬레이터 측정값: web/static/samples/spectral_*.csv 의 (white 14채널, sample 14채널). 지어낸 값이다."""
    with open(WEB / "static" / "samples" / name, encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    return [[float(r[f"ch{i}"]) for i in range(1, 15)] for r in rows[:2]]


def add_spectrum(f, white, sample, source, ts=None):
    """측정 한 번(흰 기준면 + 측정 지점)을 data/sensor.csv에 두 줄로 덧붙이고, 그 판정을 돌려준다."""
    path, stamp = DATA / "sensor.csv", ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, SENSOR_COLS)
        if new:
            w.writeheader()
        for ref, ch in (("white", white), ("sample", sample)):
            w.writerow({"location_id": loc_id(f), "measured_at": stamp, "ref": ref, "source": source,
                        **{f"ch{i}": round(v) for i, v in enumerate(ch, 1)}})
    return spectra(loc_id(f))[-1]


# ---------- 화면 공통 ----------

def phone_urls(request: Request):
    ips = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    port = request.url.port or 80
    return [f"http://{ip}:{port}/m" for ip in sorted(ips) if not ip.startswith("127.")]


def page(request, name, **ctx):
    return tpl.TemplateResponse(request, name, {"phone": phone_urls(request), **ctx})


def go(url):
    return RedirectResponse(url, status_code=303)


def record(zid):
    """구역 하나의 전체 기록 = 팀 공통 데이터 모양 (예시: shared/record.json).
    보고서 초안(make_report)의 입력이자 내보내기의 원본이다."""
    z = zone_or_404(zid)
    fs = []
    for f in q("SELECT f.*, p.src, p.kind FROM findings f JOIN photos p ON p.id = f.photo_id WHERE f.zone_id=? ORDER BY f.id", zid):
        fs.append({
            "finding_id": f"I-{f['id']:02d}", "stage": "도장 후" if f["kind"] == "post" else "도장 전",
            "status": FINDING[f["status"]], "note": f["note"],
            "score": f["score"], "box": [round(f[k], 4) for k in ("x0", "y0", "x1", "y1")],
            "photo": f["src"], "confirmed_at": f["ts"],
            "actions": [{"note": a["note"], "photo": a["src"], "at": a["ts"]}
                        for a in q("SELECT * FROM actions WHERE finding_id=? ORDER BY id", f["id"])],
            "rechecks": [{"success": bool(r["success"]), "matches": r["matches"], "photo": r["src"], "at": r["ts"]}
                         for r in q("SELECT r.*, p.src FROM rechecks r JOIN photos p ON p.id = r.photo_id "
                                    "WHERE r.finding_id=? ORDER BY r.id", f["id"])],
            "spectra": spectra(loc_id(f)), "reworks": f["reworks"], "closed_at": f["closed_at"]})
    post = one("SELECT * FROM photos WHERE zone_id=? AND kind='post' AND reviewed=1 ORDER BY id DESC", zid)
    sp = spots(post) if post else []
    return {"zone_id": z["id"], "location": zone_loc(z["id"]), "block": z["block"], "status": z["status"], "findings": fs,
            "hold_points": [{"hp": 1, "name": "도장 전 표면처리 검사", "released": z["hp1"] or ""},
                            {"hp": 2, "name": "도장 후 검사", "released": z["hp2"] or ""}],
            "recurrences": [{"pre": f"I-{a['id']:02d}", "post": f"I-{b['id']:02d}"} for b, k, a in sp if k == "재발"],
            "rejected_spots": [{"post": f"I-{b['id']:02d}", "reason": a["reason"] or ""} for b, k, a in sp if k == "반려"]}


# ---------- 시연용 예시 데이터 ----------

ZONES = ["release", "release", "fixed", "close", "wait", "close", "wait", "close",
         "close", "fixed", "badimg", "recheck", "wait", "wait", "close", "close",
         "close", "punch", "recheck", "wait", "wait", "wait", "wait", "wait"]


def seed():
    names = {v: k for k, v in STATUS.items()}
    for i, s in enumerate(ZONES, 1):
        zid = f"Z-{i:02d}"
        run("INSERT INTO zones(id, block, status, mock) VALUES(?,?,?,?)", zid, "B12P", names[s], int(zid != "Z-07"))
    for i in range(len(ZONES) + 1, 2 * len(ZONES) + 1):  # 우현 B12S: 아직 검사 전
        run("INSERT INTO zones(id, block, status, mock) VALUES(?,?,?,?)", f"Z-{i:02d}", "B12S", "검사 대기", 0)
    for k in SURFACES.keys() - {"Z"}:  # 나머지 면은 아직 검사 전
        for i in range(1, 2 * len(ZONES) + 1):
            run("INSERT INTO zones(id, block, status, mock) VALUES(?,?,?,?)", f"{k}-{i:02d}", zone_block(f"{k}-{i:02d}"),
                "검사 대기", 0)
    run("UPDATE zones SET hp1=? WHERE mock=1 AND status IN ('Close-out', 'Release 완료')", f"{INSPECTOR} · 예시")
    run("UPDATE zones SET hp2=? WHERE mock=1 AND status='Release 완료'", f"{INSPECTOR} · 예시")
    # Z-07: 검사 대기. 시연 전반에 폰으로 처음 찍는다.
    # Z-03: 지적 -> 조치 완료까지 끝난 예시. 시연 후반에 조치한 판을 다시 찍어 재검사한다.
    pid = run("INSERT INTO photos(zone_id, kind, src, w, h, glare, blur, ok, model, reviewed, ts) "
              "VALUES('Z-03', 'pre', '/static/samples/before.png', 760, 370, 0.004, 412.0, 1, 'fake-v0', 1, '10-05 14:02')")
    box = (505 / 760, 18 / 370, 672 / 760, 108 / 370)  # before.png의 녹 부위
    cid = run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score, decision, decided_at) "
              "VALUES(?,?,?,?,?,?,'accepted','10-05 14:10')", pid, *box, 0.87)
    run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score, decision, reason, decided_at) "
        "VALUES(?,0.08,0.52,0.22,0.86,0.41,'rejected','먼지·오염','10-05 14:10')", pid)  # 흰 반점(먼지·염분) 영역
    fid = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, score, status, note, ts) "
              "VALUES('Z-03',?,?,?,?,?,?,0.87,'fixed','잔류 녹 · 재블라스팅 필요','10-05 14:10')", pid, cid, *box)
    run("INSERT INTO actions(finding_id, note, src, ts) VALUES(?, '부분 재블라스팅 후 청소', NULL, '10-06 09:40')", fid)
    for ts, text in [("10-05 14:02", "도장 전 촬영 · 사진 상태 양호"), ("10-05 14:03", "AI 후보 2건"),
                     ("10-05 14:10", f"지적 I-{fid:02d} 확정 → Punch Open"),
                     ("10-06 09:40", f"I-{fid:02d} 조치 완료 등록 (작업자)")]:
        log("Z-03", text, ts, mock=1)
    seed_post("Z-04")


def seed_post(zid):
    """도장 후 단계 예시: 도장 전 지적 종결 -> 도장 후 녹 번짐 지적 -> 보수 전 분광(녹 신호) -> 작업자 조치 완료.
    시연에서는 재검사 사진(painted_fixed.png)과 보수 후 분광(spectral_after.csv)만 올리면 Release 완료까지 간다."""
    S = "/static/samples/"
    pre = run("INSERT INTO photos(zone_id, kind, src, w, h, glare, blur, ok, model, reviewed, ts) "
              "VALUES(?, 'pre', ?, 760, 370, 0.004, 412.0, 1, 'rust-color-v0', 1, '10-02 09:10')", zid, S + "before.png")
    box = (505 / 760, 18 / 370, 672 / 760, 108 / 370)
    c1 = run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score, decision, decided_at) "
             "VALUES(?,?,?,?,?,0.81,'accepted','10-02 09:12')", pre, *box)
    f1 = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, score, status, note, ts, closed_at) "
             "VALUES(?,?,?,?,?,?,?,0.81,'closed','잔류 녹 · 재블라스팅 필요','10-02 09:12','10-03 10:07')", zid, pre, c1, *box)
    run("INSERT INTO actions(finding_id, note, ts) VALUES(?, '부분 재블라스팅', '10-02 15:30')", f1)
    dust = (90 / 760, 225 / 370, 200 / 760, 290 / 370)  # before.png 아래쪽 흰 반점. 검사원이 먼지로 보고 반려
    run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score, decision, reason, decided_at) "
        "VALUES(?,?,?,?,?,0.44,'rejected','먼지·오염','10-02 09:12')", pre, *dust)
    rc = run("INSERT INTO photos(zone_id, kind, src, w, h, ok, ts) VALUES(?, 'recheck', ?, 760, 370, 1, '10-03 10:05')",
             zid, S + "after.png")
    poly = [[0.6757, 0.0338], [0.9124, 0.0741], [0.9082, 0.3395], [0.6692, 0.3043]]  # after.png 위 정답 위치
    run("INSERT INTO rechecks(finding_id, photo_id, poly, matches, success, ts) VALUES(?,?,?,1543,1,'10-03 10:05')",
        f1, rc, json.dumps(poly))
    post = run("INSERT INTO photos(zone_id, kind, src, w, h, glare, blur, ok, model, reviewed, ts) "
               "VALUES(?, 'post', ?, 760, 370, 0.0, 7.4, 1, 'rust-color-v0', 1, '10-08 14:00')", zid, S + "painted.png")
    a = align(fpath(S + "before.png"), fpath(S + "painted.png"))
    run("INSERT INTO links VALUES(?,?,?,?,?)", post, pre, json.dumps(a["homography"]), a["matches"], int(a["success"]))
    pbox = (578 / 760, 53 / 370, 624 / 760, 100 / 370)
    c2 = run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score, decision, decided_at) "
             "VALUES(?,?,?,?,?,0.99,'accepted','10-08 14:05')", post, *pbox)
    f2 = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, score, status, note, ts) "
             "VALUES(?,?,?,?,?,?,?,0.99,'fixed','녹 번짐 · 보수 도장 필요','10-08 14:05')", zid, post, c2, *pbox)
    run("INSERT INTO actions(finding_id, note, ts) VALUES(?, '부분 재블라스팅 · 보수 도장', '10-09 16:40')", f2)
    bump = (15 / 760, 235 / 370, 140 / 760, 300 / 370)  # painted.png 같은 자리 도막 요철 (검사원 직접 추가)
    c3 = run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, decision, decided_at, source) "
             "VALUES(?,?,?,?,?,'accepted','10-08 14:05','manual')", post, *bump)
    f3 = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, status, note, ts, closed_at) "
             "VALUES(?,?,?,?,?,?,?,'closed','도막 아래 이물·요철 · 부착 불량 우려','10-08 14:05','10-09 11:20')",
             zid, post, c3, *bump)
    run("INSERT INTO actions(finding_id, note, ts) VALUES(?, '요철 부위 샌딩 · 염분 확인 후 보수 도장', '10-09 11:00')", f3)
    events = [("10-02 09:10", "도장 전 촬영 · 사진 상태 양호"), ("10-02 09:12", f"지적 I-{f1:02d} 확정 → Punch Open"),
                     ("10-02 09:12", "AI 후보 반려 1건 · 먼지·오염"),
                     ("10-02 15:30", f"I-{f1:02d} 조치 완료 등록 (작업자)"),
                     ("10-03 10:05", f"I-{f1:02d} 재검사 촬영 · 자동 정합 성공 (대응점 1543개)"),
                     ("10-03 10:07", f"I-{f1:02d} 재검사 확인 → Close-out"),
                     ("10-03 10:07", f"Hold Point 1 해제 · 승인 {INSPECTOR}"), ("10-08 14:00", "도장 후 촬영 · 사진 상태 양호"),
                     ("10-08 14:00", f"도장 전 사진과 위치 맞춤 · 성공 (대응점 {a['matches']}개)"),
                     ("10-08 14:05", f"도장 후 지적 I-{f2:02d} 확정 → Punch Open"),
                     ("10-08 14:05", f"지적 I-{f3:02d} 검사원 직접 추가 (AI가 놓친 곳) · 도장 전 반려 자리 → Punch Open"),
                     ("10-09 11:00", f"I-{f3:02d} 조치 완료 등록 (작업자)"), ("10-09 11:20", f"I-{f3:02d} 재검사 확인 → Close-out"),
                     ("10-09 16:40", f"I-{f2:02d} 조치 완료 등록 (작업자)")]
    run("UPDATE zones SET hp1=? WHERE id=?", f"{INSPECTOR} · 10-03 10:07", zid)
    if SPECTRAL:
        s = add_spectrum({"zone_id": zid, "id": f2}, *sim_reading("spectral_before.csv"), "센서 시뮬레이터 (예시 값)",
                         "2026-10-09 15:20:10")
        events.insert(-1, ("10-09 15:20", f"I-{f2:02d} 분광 측정 · {s['label']} (녹 지표 {s['rust_index']})"))
    for ts, text in events:
        log(zid, text, ts, mock=1)
    refresh(zid)


with sqlite3.connect(DATA / "bt.db") as _c:
    _c.executescript(SCHEMA)
def seed_all():
    seed()
    run("UPDATE photos SET origin='합성' WHERE origin IS NULL")  # 예시 사진은 전부 가공·합성 (README '예시 사진')


if not one("SELECT 1 FROM zones"):
    seed_all()


@app.post("/demo/reset")
def demo_reset():
    """시연 초기화: 모든 기록과 올린 사진을 지우고 시연 시작 상태로 되돌린다. 서버를 끄지 않아도 된다 (리허설용)"""
    for t in q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        run(f"DELETE FROM {t['name']}")
    for f in UPLOADS.iterdir():
        f.unlink()
    ARMED.clear()
    seed_all()
    return go("/")


# ---------- 노트북 화면 ----------

def block_stats():
    """홈에 보여 줄 블록별 요약: 구역 상태 수, 미종결 지적 수"""
    out = []
    for bid, side in BLOCKS.items():
        zs = q("SELECT status FROM zones WHERE block=?", bid)
        out.append({"id": bid, "side": side, "n": len(zs), "counts": {s: sum(z["status"] == s for z in zs) for s in STATUS},
                    "open": one("SELECT COUNT(*) n FROM findings f JOIN zones z ON z.id = f.zone_id "
                                "WHERE z.block=? AND f.status != 'closed'", bid)["n"]})
    return out


@app.get("/")
def ship(request: Request):
    return page(request, "ship.html", nav="home", step=0, blocks=block_stats())


def surface_or_404(s):
    if s not in SURFACES:
        raise HTTPException(404, "없는 면")


def surface_counts(bid):
    """면별로 기록이 있는(검사 대기가 아닌) 구역 수"""
    return {k: one("SELECT COUNT(*) n FROM zones WHERE block=? AND id LIKE ? AND status != '검사 대기'", bid, f"{k}-%")["n"]
            for k in SURFACES}


@app.get("/blocks/{bid}")
def dashboard(request: Request, bid: str, s: str = "Z"):
    block_or_404(bid)
    surface_or_404(s)
    zones = q("SELECT * FROM zones WHERE block=? AND id LIKE ? ORDER BY id", bid, f"{s}-%")
    counts = {s: sum(z["status"] == s for z in zones) for s in STATUS}
    events = q("SELECT e.* FROM events e JOIN zones z ON z.id = e.zone_id WHERE z.block=? ORDER BY e.id DESC LIMIT 8", bid)
    return page(request, "dashboard.html", nav="home", step=0, bid=bid, s=s, zones=zones, counts=counts, events=events,
                surf_n=surface_counts(bid))


@app.get("/zones/{zid}")
def zone(request: Request, zid: str):
    z = zone_or_404(zid)
    photo = one("SELECT * FROM photos WHERE zone_id=? AND kind='pre' ORDER BY id DESC", zid)
    post = one("SELECT * FROM photos WHERE zone_id=? AND kind='post' ORDER BY id DESC", zid)
    findings = q("SELECT f.*, p.kind FROM findings f JOIN photos p ON p.id = f.photo_id WHERE f.zone_id=? ORDER BY f.id", zid)
    marks = [f for f in findings if photo and f["photo_id"] == photo["id"]]
    post_marks = [f for f in findings if post and f["photo_id"] == post["id"]]
    link, polys = pre_marks(post) if post else (None, [])
    sp, recurs = (spots(post) if post else []), []
    for b, k, a in sp:
        r = {"kind": k, "post": b["id"]}
        if k == "재발":
            r.update(pre=a["id"], action=(q("SELECT note FROM actions WHERE finding_id=? ORDER BY id DESC", a["id"])
                                          or [{"note": ""}])[0]["note"])
        elif k == "반려":
            r["reason"] = a["reason"]
        recurs.append(r)
    hit = {a["id"] for b, k, a in sp if k == "반려"}  # 결함이 겹친 반려 자리만 도장 전·후 사진에 점선으로
    rej_marks = []
    if hit:
        polys += [(f"도장 전 반려 · {c['reason'] or '이유 없음'}", pts) for c, pts in moved_pre(post, True)[1] if c["id"] in hit]
        rej_marks = [(f"반려 · {c['reason'] or '이유 없음'}", [[c["x0"], c["y0"]], [c["x1"], c["y0"]], [c["x1"], c["y1"]], [c["x0"], c["y1"]]])
                     for c in q("SELECT * FROM candidates WHERE decision='rejected' AND photo_id=?", photo["id"]) if c["id"] in hit]
    events = q("SELECT * FROM events WHERE zone_id=? ORDER BY id", zid)
    report = make_report(record(zid)) if findings else None
    return page(request, "zone.html", step=7 if post else ZONE_STEP[z["status"]], z=z, photo=photo, post=post,
                todo=post or photo, findings=findings, marks=marks, post_marks=post_marks, link=link, polys=polys,
                recurs=recurs, rej_marks=rej_marks, events=events, report=report)


@app.get("/review/{pid}")
def review(request: Request, pid: int):
    p = one("SELECT * FROM photos WHERE id=?", pid)
    if not p:
        raise HTTPException(404, "없는 사진")
    cands = q("SELECT * FROM candidates WHERE photo_id=? ORDER BY score DESC", pid)
    picked = {r["cand_id"] for r in q("SELECT cand_id FROM findings WHERE photo_id=?", pid)}
    polys = pre_marks(p)[1] if p["kind"] == "post" else []
    return page(request, "review.html", step=7 if p["kind"] == "post" else 2, p=p, cands=cands, picked=picked,
                polys=polys)


@app.post("/review/{pid}")
def review_confirm(pid: int, pick: list[int] = Form([]), note: str = Form(""),
                   rc: list[int] = Form([]), reason: list[str] = Form([]), manual: list[str] = Form([])):
    """검사원 판정을 라벨로 남긴다: 고른 후보 = 채택(지적), 안 고른 후보 = 반려 + 이유. rc와 reason은 후보 순서대로 짝.
    manual = AI가 놓쳐 검사원이 사진에 직접 그린 박스 "x0,y0,x1,y1" (0~1 비율). 바로 지적이 된다."""
    try:
        boxes = [[min(max(float(v), 0.0), 1.0) for v in m.split(",")] for m in manual]
        if any(len(b) != 4 or b[2] <= b[0] or b[3] <= b[1] for b in boxes):
            raise ValueError(manual)
    except ValueError:
        raise HTTPException(400, "직접 추가한 박스 형식이 잘못됐습니다")
    p = one("SELECT * FROM photos WHERE id=?", pid)
    if not p:
        raise HTTPException(404, "없는 사진")
    zid = p["zone_id"]
    if p["reviewed"]:
        return go(f"/zones/{zid}")
    why, rejected = dict(zip(rc, reason)), []
    if any(c["id"] not in pick and why.get(c["id"]) not in REASONS for c in q("SELECT id FROM candidates WHERE photo_id=?", pid)):
        raise HTTPException(400, "반려한 후보마다 이유를 골라 주세요")
    for c in q("SELECT * FROM candidates WHERE photo_id=?", pid):
        if c["id"] in pick:
            fid = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, score, status, note, ts) "
                      "VALUES(?,?,?,?,?,?,?,?,'open',?,?)",
                      zid, pid, c["id"], c["x0"], c["y0"], c["x1"], c["y1"], c["score"], note, now())
            run("UPDATE candidates SET decision='accepted', decided_at=? WHERE id=?", now(), c["id"])
            log(zid, f"{'도장 후 ' if p['kind'] == 'post' else ''}지적 I-{fid:02d} 확정 → Punch Open")
        else:
            r = why[c["id"]]
            run("UPDATE candidates SET decision='rejected', reason=?, decided_at=? WHERE id=?", r, now(), c["id"])
            rejected.append(r)
    if rejected:
        log(zid, f"AI 후보 반려 {len(rejected)}건 · " + ", ".join(rejected))
    for b in boxes:
        cid = run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, decision, decided_at, source) "
                  "VALUES(?,?,?,?,?,'accepted',?,'manual')", pid, *b, now())
        fid = run("INSERT INTO findings(zone_id, photo_id, cand_id, x0, y0, x1, y1, status, note, ts) "
                  "VALUES(?,?,?,?,?,?,?,'open',?,?)", zid, pid, cid, *b, note, now())
        log(zid, f"지적 I-{fid:02d} 검사원 직접 추가 (AI가 놓친 곳) → Punch Open")
    run("UPDATE photos SET reviewed=1 WHERE id=?", pid)
    if pick or boxes:
        refresh(zid)
    else:
        done = "Release 완료" if p["kind"] == "post" else "Close-out"
        set_status(zid, done)
        log(zid, f"검사원 확인 · 지적 없음 → {done}")
    return go(f"/zones/{zid}")


@app.get("/findings/{fid}")
def finding(request: Request, fid: int):
    f = finding_or_404(fid)
    p = one("SELECT * FROM photos WHERE id=?", f["photo_id"])
    actions = q("SELECT * FROM actions WHERE finding_id=? ORDER BY id", fid)
    r = one("SELECT * FROM rechecks WHERE finding_id=? ORDER BY id DESC", fid)
    rp = one("SELECT * FROM photos WHERE id=?", r["photo_id"]) if r else None
    step = 7 if p["kind"] == "post" else ZONE_STEP[FINDING[f["status"]]]
    return page(request, "finding.html", step=step, f=f, p=p, actions=actions, r=r, rp=rp,
                poly=json.loads(r["poly"]) if r else [], z=zone_or_404(f["zone_id"]), loc=loc_id(f), spec=spectra(loc_id(f)),
                sim=SENSOR_MODE == "sim", armed=ARMED.get("fid") == fid, spectral=SPECTRAL)


@app.post("/findings/{fid}/close")
def finding_close(fid: int):
    f = finding_or_404(fid)
    if f["status"] != "recheck":  # 이미 처리됐거나 재검사 사진이 없다
        return go(f"/findings/{fid}")
    run("UPDATE findings SET status='closed', closed_at=? WHERE id=?", now(), fid)
    log(f["zone_id"], f"I-{fid:02d} 재검사 확인 → Close-out")
    refresh(f["zone_id"])
    return go(f"/zones/{f['zone_id']}")


@app.post("/findings/{fid}/reopen")
def finding_reopen(fid: int):
    f = finding_or_404(fid)
    if f["status"] != "recheck":
        return go(f"/findings/{fid}")
    run("UPDATE findings SET status='open', reworks=reworks + 1 WHERE id=?", fid)
    log(f["zone_id"], f"I-{fid:02d} 재검사 결과 미흡 → 재조치 (Punch Open)")
    refresh(f["zone_id"])
    return go(f"/zones/{f['zone_id']}")


@app.get("/zones/{zid}/export.csv")
def export(zid: str):
    rec = record(zid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["구역", "위치", "지적", "단계", "상태", "내용", "AI 점수", "확정 시각", "조치", "재검사 정합", "대응점", "분광 녹 지표"])
    for f in rec["findings"]:
        a = f["actions"][-1] if f["actions"] else {}
        r = f["rechecks"][-1] if f["rechecks"] else {}
        sp = " → ".join(str(s["rust_index"]) for s in f["spectra"])
        w.writerow([rec["zone_id"], rec["location"], f["finding_id"], f["stage"], f["status"], f["note"], f["score"], f["confirmed_at"],
                    a.get("note", ""), ("성공" if r.get("success") else "실패") if r else "", r.get("matches", ""), sp])
    w.writerow([])
    w.writerow(["시각", "기록", "예시 데이터"])
    for e in q("SELECT * FROM events WHERE zone_id=? ORDER BY id", zid):
        w.writerow([e["ts"], e["text"], "예시" if e["mock"] else ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{zid}.csv"'})


def label_rows():
    """판정 라벨: 검사원이 판단한 AI 후보 하나당 한 줄. 채택이면 그 지적의 조치 결과가 붙고,
    도장 후 사진이 있으면 도장 전 판단(채택·반려)이 도장 후 결과로 채점된다 (same_spot)."""
    pair = {}  # 후보 id -> 도장 전·후 같은 자리 관계
    for post in q("SELECT * FROM photos WHERE kind='post' AND reviewed=1"):
        for b, k, a in spots(post):
            if k == "재발":
                pair[a["cand_id"]] = f"도장 후 I-{b['id']:02d} 재발"
                pair.setdefault(b["cand_id"], f"도장 전 I-{a['id']:02d} 자리")
            elif k == "반려":
                pair[a["id"]] = f"도장 후 I-{b['id']:02d} 결함 (반려했던 자리)"
                pair.setdefault(b["cand_id"], "도장 전 반려 자리")
            else:
                pair[b["cand_id"]] = "도장 전 기록 없음"
        # 도장 후 사진 안에 들어왔는데 결함이 안 겹친 도장 전 판단: 채택이면 조치가 버텼고, 반려면 반려가 맞았다
        found, rej = moved_pre(post)[1], moved_pre(post, True)[1]
        for cid, pts in [(f["cand_id"], p) for f, p in found] + [(c["id"], p) for c, p in rej]:
            if 0 <= sum(x for x, _ in pts) / 4 <= 1 and 0 <= sum(y for _, y in pts) / 4 <= 1:
                pair.setdefault(cid, "도장 후 이상 없음")
    rows = []
    for c in q("SELECT c.*, p.zone_id, p.kind, p.src, p.w, p.h, p.model, p.origin, z.mock, f.id AS fid, f.status AS fstatus, "
               "f.reworks FROM candidates c JOIN photos p ON p.id = c.photo_id JOIN zones z ON z.id = p.zone_id "
               "LEFT JOIN findings f ON f.cand_id = c.id WHERE c.decision IS NOT NULL ORDER BY c.id"):
        rows.append({
            "label_id": c["id"], "photo_id": c["photo_id"], "src": c["src"], "w": c["w"], "h": c["h"],
            "zone": c["zone_id"], "location": zone_loc(c["zone_id"]), "stage": "도장 후" if c["kind"] == "post" else "도장 전",
            "model": c["model"] or "", "score": c["score"], "box": [round(c[k], 4) for k in ("x0", "y0", "x1", "y1")],
            "source": "검사원 직접" if c["source"] == "manual" else "AI", "origin": c["origin"] or "",
            "decision": "채택" if c["decision"] == "accepted" else "반려", "reason": c["reason"] or "",
            "finding": f"I-{c['fid']:02d}" if c["fid"] else "",
            "outcome": "" if not c["fid"] else ("종결" if c["fstatus"] == "closed" else "진행 중"),
            "reworks": c["reworks"] or 0, "same_spot": pair.get(c["id"], ""), "decided_at": c["decided_at"],
            "example": bool(c["mock"])})
    return rows


@app.get("/data")
def data(request: Request):
    rows = label_rows()
    acc = [r for r in rows if r["decision"] == "채택" and r["source"] == "AI"]
    ai = [r for r in rows if r["source"] == "AI"]
    why = Counter(r["reason"] for r in rows if r["decision"] == "반려")
    fs = q("SELECT status, reworks FROM findings")
    tiles = {"labels": len(rows), "accept": f"{len(acc)} / {len(ai)}",
             "top": why.most_common(1)[0][0] if why else "-",
             "rework": f"{sum(f['reworks'] > 0 for f in fs)} / {len(fs)}",
             "recur": sum(r["same_spot"].endswith("재발") for r in rows),
             "rejhit": sum("반려했던 자리" in r["same_spot"] for r in rows), "example": sum(r["example"] for r in rows),
             "missed": sum(r["source"] == "검사원 직접" for r in rows)}
    origins = Counter(r["origin"] for r in rows)
    return page(request, "data.html", nav="data", rows=rows[::-1][:12], tiles=tiles, origins=[(k, origins[k]) for k in ORIGINS],
                reasons=[(k, why[k]) for k in REASONS], most=max(why.values(), default=1))


@app.get("/data/export.zip")
def export_dataset():
    """학습 데이터 내보내기: images/ + annotations.json(COCO, 결함 = 채택·직접 추가만) + rejected.json(반려 박스, 같은 모양)
    + labels.csv. 반려 박스를 결함 파일에 섞으면 attributes를 안 읽는 보통 COCO 로더가 반려까지 결함으로 학습한다."""
    rows, images, anns, rejected = label_rows(), {}, [], []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            w, h = r["w"], r["h"]
            if r["photo_id"] not in images:
                name = f"images/{r['photo_id']:04d}{Path(r['src']).suffix}"
                zf.write(fpath(r["src"]), name)
                images[r["photo_id"]] = {"id": r["photo_id"], "file_name": name, "width": w, "height": h,
                                         "zone": r["zone"], "location": r["location"], "stage": r["stage"], "origin": r["origin"]}
            x0, y0, x1, y1 = r["box"]
            (anns if r["decision"] == "채택" else rejected).append({"id": r["label_id"], "image_id": r["photo_id"], "category_id": 1, "iscrowd": 0,
                         "bbox": [round(x0 * w, 1), round(y0 * h, 1), round((x1 - x0) * w, 1), round((y1 - y0) * h, 1)],
                         "area": round((x1 - x0) * w * (y1 - y0) * h, 1),
                         "attributes": {k: r[k] for k in ("source", "decision", "reason", "stage", "zone", "location", "model", "score",
                                                          "finding", "outcome", "reworks", "same_spot", "example")}})
        zf.writestr("annotations.json", json.dumps({
            "info": {"description": "BlastTrace 검사원 판정 라벨 · 결함(채택·검사원 직접 추가)만. 반려 박스는 rejected.json"},
            "images": list(images.values()), "categories": [{"id": 1, "name": "녹·결함"}], "annotations": anns},
            ensure_ascii=False, indent=1))
        zf.writestr("rejected.json", json.dumps({
            "info": {"description": "검사원이 결함 아님으로 반려한 AI 후보 (헷갈리는 음성)",
                     "same_spot": "'반려했던 자리'가 붙은 박스는 도장 후 그 자리에서 결함이 나왔다. 음성으로 쓰지 않는다"},
            "images": list(images.values()), "categories": [{"id": 1, "name": "반려 · 결함 아님"}], "annotations": rejected},
            ensure_ascii=False, indent=1))
        cols = ["label_id", "zone", "location", "stage", "origin", "source", "decision", "reason", "score", "model", "finding", "outcome",
                "reworks", "same_spot", "decided_at", "example", "photo_id", "box"]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(cols)
        w.writerows([[r[k] for k in cols] for r in rows])
        zf.writestr("labels.csv", "\ufeff" + out.getvalue())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="blasttrace_dataset.zip"'})


@app.get("/api/v")
def version():
    """화면 새로 고침용 버전 = 마지막 이력 번호. 화면은 이 값이 바뀌었을 때만 다시 그린다"""
    p = one("SELECT id, zone_id, src, kind, ok FROM photos ORDER BY id DESC")
    return {"v": one("SELECT MAX(id) v FROM events")["v"] or 0, "photo": dict(p) if p else None}


@app.get("/api/zones/{zid}/record")
def api_record(zid: str):
    """make_report에 들어가는 입력을 그대로 본다. LLM 담당이 실제 입력 예시를 받을 때 쓴다."""
    return record(zid)


# ---------- 폰 화면 ----------

@app.get("/m")
def m_home(request: Request):
    return page(request, "m_home.html", blocks=block_stats())


@app.get("/m/blocks/{bid}")
def m_block(request: Request, bid: str, s: str = "Z"):
    block_or_404(bid)
    surface_or_404(s)
    return page(request, "m_block.html", bid=bid, s=s, zones=q("SELECT * FROM zones WHERE block=? AND id LIKE ? ORDER BY id", bid, f"{s}-%"))


@app.get("/m/{zid}")
def m_zone(request: Request, zid: str, msg: str = ""):
    z = zone_or_404(zid)
    findings = q("SELECT f.*, p.kind, p.src FROM findings f JOIN photos p ON p.id = f.photo_id WHERE f.zone_id=? ORDER BY f.id", zid)
    last = {f["id"]: (spectra(loc_id(f)) or [None])[-1] for f in findings if f["kind"] == "post"}
    todo = one("SELECT * FROM photos WHERE zone_id=? AND ok=1 AND reviewed=0 AND kind IN ('pre', 'post') ORDER BY id DESC", zid)
    rcs = {}
    for f in findings:
        r = one("SELECT r.poly, p.src FROM rechecks r JOIN photos p ON p.id = r.photo_id WHERE r.finding_id=? ORDER BY r.id DESC",
                f["id"]) if f["status"] == "recheck" else None
        if r:
            rcs[f["id"]] = {"src": r["src"], "poly": json.loads(r["poly"])}
    return page(request, "m_zone.html", z=z, findings=findings, msg=msg, post=post_phase(z), last=last, todo=todo, rcs=rcs,
                todo_c=q("SELECT * FROM candidates WHERE photo_id=? ORDER BY score DESC", todo["id"]) if todo else [],
                sim=SENSOR_MODE == "sim", spectral=SPECTRAL)


@app.post("/m/{zid}/photo")
def m_photo(zid: str, photo: UploadFile = File(...)):
    z = zone_or_404(zid)
    kind = "post" if post_phase(z) else "pre"
    stage = "도장 후" if kind == "post" else "도장 전"
    src, (w, h) = save(photo)
    origin = origin_of(fpath(src))
    qa = check_quality(fpath(src))
    if not qa["ok"]:
        run("INSERT INTO photos(zone_id, kind, src, w, h, glare, blur, ok, ts, origin) VALUES(?,?,?,?,?,?,?,0,?,?)",
            zid, kind, src, w, h, qa["glare"], qa["blur"], now(), origin)
        set_status(zid, "촬영 불량")
        log(zid, f"{stage} 촬영 · 반사 {qa['glare']:.1%} → 재촬영 필요")
        return go(f"/m/{zid}?msg=반사가 많습니다({qa['glare']:.1%}). 각도를 바꿔 다시 찍어주세요")
    out = predict(fpath(src))
    pid = run("INSERT INTO photos(zone_id, kind, src, w, h, glare, blur, ok, model, ts, origin) VALUES(?,?,?,?,?,?,?,1,?,?,?)",
              zid, kind, src, w, h, qa["glare"], qa["blur"], out["model_version"], now(), origin)
    for c in out["candidates"]:
        run("INSERT INTO candidates(photo_id, x0, y0, x1, y1, score) VALUES(?,?,?,?,?,?)", pid, *norm_box(c, w, h), c["score"])
    set_status(zid, "검토 대기")
    log(zid, f"{stage} 촬영 · 사진 상태 양호")
    pre = one("SELECT * FROM photos WHERE zone_id=? AND kind='pre' AND reviewed=1 ORDER BY id DESC", zid)
    if kind == "post" and pre:
        a = align(fpath(pre["src"]), fpath(src))
        run("INSERT INTO links VALUES(?,?,?,?,?)", pid, pre["id"], json.dumps(a["homography"]), a["matches"], int(a["success"]))
        log(zid, "도장 전 사진과 위치 맞춤 · " + (f"성공 (대응점 {a['matches']}개)" if a["success"] else "실패 → 검사원 확인"))
    log(zid, f"AI 후보 {len(out['candidates'])}건 ({out['model_version']})")
    return go(f"/m/{zid}?msg=올렸습니다. 노트북에서 AI 후보를 확인하세요")


@app.post("/m/findings/{fid}/measure")
def m_measure(fid: int):
    """폰에서 '분광 측정'. sim 모드는 바로 시연값을 넣고, device 모드는 센서가 /api/sensor 로 보낼 때까지 기다린다."""
    f = finding_or_404(fid)
    if SENSOR_MODE != "sim":
        ARMED["fid"] = fid
        return go(f"/m/{f['zone_id']}?msg=센서를 I-{fid:02d} 위치에 대고 측정 버튼을 누르세요")
    # ponytail: 시뮬레이터는 조치 전이면 녹 번짐, 조치 후면 정상 도막 값을 낸다. 실제 센서가 오면 BT_SENSOR=device
    s = add_spectrum(f, *sim_reading("spectral_before.csv" if f["status"] == "open" else "spectral_after.csv"),
                     "센서 시뮬레이터 (예시 값)")
    log(f["zone_id"], f"I-{fid:02d} 분광 측정 · {s['label']} (녹 지표 {s['rust_index']})")
    return go(f"/m/{f['zone_id']}?msg=분광 측정 · {s['label']} (녹 지표 {s['rust_index']})")


@app.post("/api/sensor")
def api_sensor(data: dict = Body(...)):
    """센서가 측정할 때마다 보낸다: {"white": [14채널], "sample": [14채널], "device": "AS7343-01"}.
    폰에서 '분광 측정'을 누른 지적에 붙는다. 형식: sensor/README.md"""
    if "fid" not in ARMED:
        raise HTTPException(409, "폰에서 '분광 측정'을 먼저 누르세요")
    try:
        white, sample = ([float(v) for v in data[k]] for k in ("white", "sample"))
        if len(white) != 14 or len(sample) != 14:
            raise ValueError("채널 수")
    except Exception:
        raise HTTPException(400, "white, sample 은 각각 14개 숫자여야 합니다")
    f = finding_or_404(ARMED.pop("fid"))
    s = add_spectrum(f, white, sample, str(data.get("device") or "센서"))
    log(f["zone_id"], f"I-{f['id']:02d} 분광 측정 · {s['label']} (녹 지표 {s['rust_index']})")
    return {"finding": f"I-{f['id']:02d}", "label": s["label"], "rust_index": s["rust_index"]}


@app.post("/m/findings/{fid}/action")
def m_action(fid: int, note: str = Form(""), photo: UploadFile | None = File(None)):
    f = finding_or_404(fid)
    if f["status"] != "open":  # 폰에 옛 화면이 남아 있어도 종결된 지적을 '조치 완료'로 되돌리지 않는다
        return go(f"/m/{f['zone_id']}?msg=이미 처리된 지적입니다 (I-{fid:02d})")
    src = save(photo)[0] if photo and photo.filename else None
    run("INSERT INTO actions(finding_id, note, src, ts) VALUES(?,?,?,?)", fid, note, src, now())
    run("UPDATE findings SET status='fixed' WHERE id=?", fid)
    log(f["zone_id"], f"I-{fid:02d} 조치 완료 등록 (작업자)")
    refresh(f["zone_id"])
    return go(f"/m/{f['zone_id']}?msg=조치 완료를 등록했습니다")


@app.post("/m/findings/{fid}/recheck")
def m_recheck(fid: int, photo: UploadFile = File(...)):
    f = finding_or_404(fid)
    if f["status"] != "fixed":
        return go(f"/m/{f['zone_id']}?msg=이미 처리된 지적입니다 (I-{fid:02d})")
    prev = one("SELECT * FROM photos WHERE id=?", f["photo_id"])
    src, (w, h) = save(photo)
    pid = run("INSERT INTO photos(zone_id, kind, src, w, h, ok, ts, origin) VALUES(?, 'recheck',?,?,?,1,?,?)",
              f["zone_id"], src, w, h, now(), origin_of(fpath(src)))
    out = align(fpath(prev["src"]), fpath(src))
    box = (f["x0"], f["y0"], f["x1"], f["y1"])
    poly = move_box(box, out["homography"], (prev["w"], prev["h"]), (w, h)) if out["success"] else []
    run("INSERT INTO rechecks(finding_id, photo_id, poly, matches, success, ts) VALUES(?,?,?,?,?,?)",
        fid, pid, json.dumps(poly), out["matches"], int(out["success"]), now())
    run("UPDATE findings SET status='recheck' WHERE id=?", fid)
    res = f"자동 정합 성공 (대응점 {out['matches']}개)" if out["success"] else "자동 정합 실패 → 검사원 수동 확인"
    log(f["zone_id"], f"I-{fid:02d} 재검사 촬영 · {res}")
    refresh(f["zone_id"])
    return go(f"/m/{f['zone_id']}?msg=재검사 사진을 올렸습니다. {res}")
