"""시연 흐름 ①~⑦이 끝까지 도는지 확인한다. 실행: python test_flow.py (서버를 켤 필요 없음)
PR 합치기 전에 한 번씩 돌린다. 임시 폴더에 DB를 새로 만들어서 실제 data/는 건드리지 않는다."""
import io
import json
import os
import tempfile
import zipfile

os.environ["BT_DATA"] = tempfile.mkdtemp()
os.environ["BT_SPECTRAL"] = "1"  # 데모 화면에선 꺼져 있지만, 코드는 계속 검사한다

from fastapi.testclient import TestClient  # noqa: E402

from server import app as bt  # noqa: E402

c = TestClient(bt.app)
S = "web/static/samples/"


def status(zid):
    return bt.one("SELECT status FROM zones WHERE id=?", zid)["status"]


def upload(url, path, **data):
    with open(path, "rb") as f:
        r = c.post(url, files={"photo": (os.path.basename(path), f, "image/png")}, data=data)
    assert r.status_code == 200, (url, r.status_code, r.text[:200])


for url in ["/", "/blocks/B12P", "/blocks/B12S", "/m", "/m/blocks/B12P", "/m/blocks/B12S", "/m/Z-07", "/m/Z-31", "/zones/Z-03",
            "/api/zones/Z-03/record"]:
    assert c.get(url).status_code == 200, url
# 홈 = 블록 분할도. 데이터가 있는 B12 좌현(P)·우현(S)만 평면도로 들어가고, 나머지 블록은 예시라 404
home, mhome = c.get("/").text, c.get("/m").text
assert 'class="s3"' in home and "/static/ship3d.js" in home and 'class="s3' not in mhome  # 3D 선박은 노트북만 (폰은 현장용, 인터넷 없어도)
assert 'class="segno"' in home and "최근 구역" not in mhome  # 폰 접속 QR · 시연 전엔 최근 구역 없음
assert all(f'href="/blocks/{b}"' in home and f'href="/m/blocks/{b}"' in mhome for b in ("B12P", "B12S"))
assert c.get("/blocks/B13P").status_code == 404 and c.get("/blocks/B12").status_code == 404
assert 'href="/zones/Z-31"' in c.get("/blocks/B12S").text and 'href="/zones/Z-07"' not in c.get("/blocks/B12S").text

# 블록 평면도: 칸 = 구역, 위치는 Fr/L로 부른다 (Z-07 = 위 줄 오른쪽에서 두 번째 칸)
assert bt.zone_loc("Z-07") == "이중저 밸러스트 탱크 · Fr.216–218 · L1–L3 (P)"
assert bt.zone_loc("Z-31") == "이중저 밸러스트 탱크 · Fr.216–218 · L1–L3 (S)"
assert bt.zone_loc("H-07") == "하부 호퍼 경사판 · Fr.216–218 · HL1–HL3 (P)"
# 블록 안의 면 고르기: 면마다 구역이 따로 있고(?s=), 없는 면은 404
assert bt.one("SELECT COUNT(*) n FROM zones")["n"] == 4 * 48
hop = c.get("/blocks/B12P?s=H").text
assert 'href="/zones/H-07"' in hop and 'href="/zones/Z-07"' not in hop and 'href="/blocks/B12P?s=D"' in hop
assert 'href="/m/O-31"' in c.get("/m/blocks/B12S?s=O").text and c.get("/blocks/B12P?s=X").status_code == 404
upload("/m/H-07/photo", S + "before.png")  # 다른 면도 같은 흐름이 돈다
assert status("H-07") == "검토 대기" and "하부 호퍼 경사판 · Fr.216–218" in c.get("/zones/H-07").text
assert bt.zone_cell("Z-07") == (2, 6) and bt.zone_cell("Z-31") == (0, 6)  # 좌현은 위에서 볼 때 L1 줄이 아래
assert 'href="/m/Z-07"' in c.get("/m/blocks/B12P").text and 'href="/zones/Z-07"' in c.get("/blocks/B12P").text

# ① 도장 전 촬영 (Z-07). 화면은 이력 번호(/api/v)가 바뀔 때만 다시 그린다
v0 = c.get("/api/v").json()["v"]
upload("/m/Z-07/photo", S + "before.png")
assert c.get("/api/v").json()["v"] > v0 and c.get("/api/v").json()["photo"]["zone_id"] == "Z-07"
assert "<img" in c.get("/m/Z-07").text  # 폰에도 방금 올린 사진과 AI 후보가 보인다
assert status("Z-07") == "검토 대기"
upload("/m/Z-07/photo", S + "before.png")  # 폰에서 두 번 올려도(옛 화면·뒤로 가기) 판정 전에는 받지 않는다
assert bt.one("SELECT COUNT(*) n FROM photos WHERE zone_id='Z-07'")["n"] == 1
pid = bt.one("SELECT id FROM photos WHERE zone_id='Z-07'")["id"]

# ② AI 후보 -> ③ 지적 확정
cands = [r["id"] for r in bt.q("SELECT id FROM candidates WHERE photo_id=?", pid)]
# 반려는 이유까지 골라야 한다: 판정하지 않은 후보가 있으면 거절하고 아무것도 바꾸지 않는다
assert c.post(f"/review/{pid}", data={"pick": cands[:1], "rc": cands, "reason": [""] * len(cands)}).status_code == 400
assert status("Z-07") == "검토 대기"
assert cands and "confirm(" in c.get(f"/review/{pid}").text  # 아무것도 안 고르면 HP 해제 확인창
# 고른 후보 = 채택(지적), 안 고른 후보 = 반려 + 이유 -> 판정 라벨
c.post(f"/review/{pid}", data={"pick": cands[:1], "note": "잔류 녹", "rc": cands, "cnote": ["용접부 옆 잔류 녹"] + [""] * (len(cands) - 1),
                              "reason": ["기타"] + ["그림자·반사"] * (len(cands) - 1)})
dec = {r["id"]: (r["decision"], r["reason"]) for r in bt.q("SELECT * FROM candidates WHERE photo_id=?", pid)}
assert dec[cands[0]] == ("accepted", None) and all(dec[k] == ("rejected", "그림자·반사") for k in cands[1:]), dec
assert status("Z-07") == "Punch Open"
f0 = bt.one("SELECT * FROM findings WHERE zone_id='Z-07'")
fid = f0["id"]
assert f0["note"] == "용접부 옆 잔류 녹"  # 카드에 따로 적은 내용이 그 지적에 들어간다 (비우면 공통 내용)
assert f"I-{fid:02d} 조치 후 완료 등록" in c.get("/zones/Z-07").text and "최근 구역 Z-07" in c.get("/m").text

# ④ 조치 (작업자, 사진 없이)
r = c.post(f"/m/findings/{fid}/action", data={"note": "부분 재블라스팅"})
assert r.status_code == 200 and status("Z-07") == "조치 완료"

# 폰 지적 카드에 사진과 위치 박스가 보인다
assert "<img" in c.get("/m/Z-03").text and "<img" in c.get("/m/Z-07").text

# ⑤ 재검사: Z-03 예시(before.png 지적)에 after.png를 올리면 실제 정합으로 박스가 옮겨져야 한다
f3 = bt.one("SELECT id FROM findings WHERE zone_id='Z-03'")["id"]
upload(f"/m/findings/{f3}/recheck", S + "after.png")
rc = bt.one("SELECT * FROM rechecks WHERE finding_id=?", f3)
poly = json.loads(rc["poly"])
gt = [[513.5 / 760, 12.5 / 370], [693.4 / 760, 27.4 / 370], [690.2 / 760, 125.6 / 370], [508.6 / 760, 112.6 / 370]]
err = max(abs(a - b) for p, g in zip(poly, gt) for a, b in zip(p, g))
assert rc["success"] and err < 0.01, (rc["matches"], err)
assert status("Z-03") == "재검사 대기"
assert c.get(f"/findings/{f3}").status_code == 200

# 재조치 -> 다시 재검사 대기로 돌리고 Close-out
c.post(f"/findings/{f3}/reopen")
assert bt.one("SELECT reworks FROM findings WHERE id=?", f3)["reworks"] == 1
assert status("Z-03") == "Punch Open"
bt.run("UPDATE findings SET status='recheck' WHERE id=?", f3)
c.post(f"/findings/{f3}/close")
assert bt.one("SELECT closed_at FROM findings WHERE id=?", f3)["closed_at"]
# 폰에 옛 화면이 남아 있어도 종결된 지적이 '조치 완료'로 되돌아가지 않는다
c.post(f"/m/findings/{f3}/action", data={"note": "옛 화면"})
c.post(f"/findings/{f3}/reopen")
assert bt.one("SELECT status FROM findings WHERE id=?", f3)["status"] == "closed"
assert status("Z-03") == "Close-out"
# Hold Point 1: 검사원이 Close-out하는 순간 해제, 승인자·시각이 남고 AI는 해제하지 않는다
z3 = bt.one("SELECT * FROM zones WHERE id='Z-03'")
assert z3["hp1"].startswith(bt.INSPECTOR) and not z3["hp2"]
assert "Hold Point 1 해제" in c.get("/zones/Z-03").text and "도장 착수 금지" in c.get("/m/Z-07").text

# ⑥ 이력·보고서·내보내기
z = c.get("/zones/Z-03").text
assert "보고서 초안" in z and f"I-{f3:02d}" in z
csv_text = c.get("/zones/Z-03/export.csv").text
assert f"I-{f3:02d}" in csv_text and "성공" in csv_text

# 분광은 도장 후 지적에만 붙는다
assert "분광 · 같은 위치 도막 측정" not in c.get(f"/findings/{f3}").text

# ⑦ 도장 후: Close-out된 Z-03에 도장 후 사진(after.png와 같은 각도) -> 도장 전 지적 위치가 같은 자리로 옮겨지고
#    재블라스팅했던 자리의 녹 번짐이 후보로 잡힌다
assert "도장 후 촬영" in c.get("/m/Z-03").text
upload("/m/Z-03/photo", S + "painted.png")
post = bt.one("SELECT * FROM photos WHERE zone_id='Z-03' AND kind='post'")
link, polys = bt.pre_marks(post)
err2 = max(abs(a - b) for p, g in zip(polys[0][1], gt) for a, b in zip(p, g))
assert link["success"] and err2 < 0.01, (link["matches"], err2)
assert status("Z-03") == "검토 대기" and c.get(f"/review/{post['id']}").status_code == 200
pc = bt.q("SELECT * FROM candidates WHERE photo_id=?", post["id"])
xs, ys = [x for x, _ in polys[0][1]], [y for _, y in polys[0][1]]
assert len(pc) == 1 and min(xs) < (pc[0]["x0"] + pc[0]["x1"]) / 2 < max(xs) and min(ys) < (pc[0]["y0"] + pc[0]["y1"]) / 2 < max(ys)
assert "자리 · 재발 의심" in c.get(f"/review/{post['id']}").text  # 판정하는 자리에서 도장 전 기록이 보인다
c.post(f"/review/{post['id']}", data={"pick": [pc[0]["id"]], "note": "녹 번짐"})
assert status("Z-03") == "Punch Open"
f4 = bt.one("SELECT id FROM findings WHERE photo_id=?", post["id"])["id"]
# 보수 도장 후 다른 각도로 다시 찍은 사진 -> 녹 번짐 위치를 찾아 겹친다 (정답: painted_fixed.png를 만든 원근 변환)
# 분광: 폰에서 '분광 측정'을 누르면 값이 서버로 바로 들어온다 (sim: 조치 전 녹 번짐, 조치 후 정상 도막 시연값)
assert "분광 · 같은 위치 도막 측정" in c.get(f"/findings/{f4}").text
c.post(f"/m/findings/{f4}/measure")
c.post(f"/m/findings/{f4}/action", data={"note": "부분 재블라스팅 · 보수 도장"})
upload(f"/m/findings/{f4}/recheck", S + "painted_fixed.png")
c.post(f"/m/findings/{f4}/measure")
sp = bt.spectra(f"Z-03-I-{f4:02d}")
assert [(s["rust_index"], s["label"]) for s in sp] == [(2.46, "녹 신호"), (1.08, "녹 신호 없음")], sp
fp4 = c.get(f"/findings/{f4}").text
assert "✓ 녹 신호 없음" in fp4 and "<polyline" in fp4

bt.SPECTRAL = False  # 기본(데모)에서는 분광 화면이 안 보인다
assert "분광 · 같은 위치 도막 측정" not in c.get(f"/findings/{f4}").text
assert "분광 측정" not in c.get("/m/Z-03").text
bt.SPECTRAL = True

# device 모드: 폰이 지적을 고르고(분광 측정), 실제 센서가 /api/sensor 로 측정값을 보낸다
bt.SENSOR_MODE = "device"
assert c.post("/api/sensor", json={"white": [1] * 14, "sample": [1] * 14}).status_code == 409
c.post(f"/m/findings/{f4}/measure")
assert c.post("/api/sensor", json={"white": [1] * 3, "sample": [1] * 14}).status_code == 400
w, smp = bt.sim_reading("spectral_before.csv")
r = c.post("/api/sensor", json={"white": w, "sample": smp, "device": "AS7343-01"})
assert r.status_code == 200 and r.json()["label"] == "녹 신호", r.text
assert bt.spectra(f"Z-03-I-{f4:02d}")[-1]["source"] == "AS7343-01"
bt.SENSOR_MODE = "sim"
rc4 = bt.one("SELECT * FROM rechecks WHERE finding_id=?", f4)
gt4 = [[0.736, 0.1309], [0.8011, 0.128], [0.8043, 0.2596], [0.7393, 0.2617]]
err4 = max(abs(a - b) for p, g in zip(json.loads(rc4["poly"]), gt4) for a, b in zip(p, g))
assert rc4["success"] and err4 < 0.01, (rc4["matches"], err4)
c.post(f"/findings/{f4}/close")
assert status("Z-03") == "Release 완료" and bt.one("SELECT hp2 FROM zones WHERE id='Z-03'")["hp2"]
z = c.get("/zones/Z-03").text
assert "도장 후 사진" in z and "도장 전 I-" in z and "[도장 후 · Close-out] 녹 번짐" in z

# Z-04 예시: 도장 후 지적이 보수 전 분광(녹 신호)까지 들어 있다. 재검사 사진 + 보수 후 분광만 올리면 Release 완료
f5 = bt.one("SELECT f.id FROM findings f JOIN photos p ON p.id = f.photo_id WHERE f.zone_id='Z-04' AND p.kind='post'")["id"]
assert status("Z-04") == "조치 완료" and "녹 지표 2.46" in c.get(f"/findings/{f5}").text
upload(f"/m/findings/{f5}/recheck", S + "painted_fixed.png")
c.post(f"/m/findings/{f5}/measure")
assert [s["label"] for s in bt.spectra(f"Z-04-I-{f5:02d}")] == ["녹 신호", "녹 신호 없음"]
c.post(f"/findings/{f5}/close")
assert status("Z-04") == "Release 완료"

# 판정 데이터: 도장 후 녹 번짐(I-f4)이 도장 전 지적(I-f3) 자리와 겹치면 '같은 자리 재발'
post3 = bt.one("SELECT * FROM photos WHERE zone_id='Z-03' AND kind='post'")
assert [(a["id"], b["id"]) for a, b in bt.recur(post3)] == [(f3, f4)]
assert "같은 자리 재발" in c.get("/zones/Z-03").text
rows = bt.label_rows()
lab = {r["finding"]: r for r in rows if r["finding"]}
assert lab[f"I-{f3:02d}"]["same_spot"] == f"도장 후 I-{f4:02d} 재발" and lab[f"I-{f3:02d}"]["reworks"] == 1
assert any(r["reason"] == "그림자·반사" for r in rows) and "채택률" in c.get("/data").text
z = zipfile.ZipFile(io.BytesIO(c.get("/data/export.zip").content))
coco, rej = json.loads(z.read("annotations.json")), json.loads(z.read("rejected.json"))
assert len(coco["annotations"]) == sum(r["decision"] == "채택" for r in rows) and all(i["file_name"] in z.namelist() for i in coco["images"])
assert rej["annotations"] and all(a["attributes"]["decision"] == "반려" for a in rej["annotations"])
assert len(coco["annotations"]) + len(rej["annotations"]) == len(rows)

# 도장 후 결과로 도장 전 판단 채점: Z-04는 먼지로 반려한 흰 반점 자리에서 도장 후 요철, Z-03은 반려 자리 이상 없음
lab4 = [r for r in rows if r["zone"] == "Z-04"]
assert any(r["decision"] == "반려" and "반려했던 자리" in r["same_spot"] for r in lab4), lab4
assert any(r["same_spot"] == "도장 전 반려 자리" for r in lab4)
assert any(r["zone"] == "Z-03" and r["decision"] == "반려" and r["same_spot"] == "도장 후 이상 없음" for r in rows)
assert "도장 전에 반려한 자리에서" in c.get("/zones/Z-04").text and "반려했던 자리 1" in c.get("/data").text
assert "반려 판단 재확인" in bt.make_report(bt.record("Z-04"))["text"]
assert "labels.csv" in z.namelist()

# AI가 놓친 결함: 검사원이 사진에 직접 그린 박스는 후보 없이도 바로 지적이 된다 (Z-13)
upload("/m/Z-13/photo", S + "before.png")
p13 = bt.one("SELECT id FROM photos WHERE zone_id='Z-13'")["id"]
assert c.post(f"/review/{p13}", data={"manual": ["1,1,0,0"]}).status_code == 400
c13 = [r["id"] for r in bt.q("SELECT id FROM candidates WHERE photo_id=?", p13)]
c.post(f"/review/{p13}", data={"manual": ["0.1,0.6,0.2,0.8"], "note": "용접부 언더컷 주변 녹", "rc": c13, "reason": ["용접 비드"] * len(c13)})
m = bt.one("SELECT f.id, c.source, f.score FROM findings f JOIN candidates c ON c.id = f.cand_id WHERE f.zone_id='Z-13'")
assert m["source"] == "manual" and m["score"] is None and status("Z-13") == "Punch Open"
assert any(r["source"] == "검사원 직접" and r["finding"] == f"I-{m['id']:02d}" for r in bt.label_rows())
assert "검사원 직접 추가" in c.get("/zones/Z-13").text and "AI가 놓친 결함" in c.get("/data").text

# 시연 대본: Z-07 한 구역으로 ①~⑦ 끝까지 (시연사진 1 -> 2 -> 3 -> 4)
upload(f"/m/findings/{fid}/recheck", S + "after.png")
c.post(f"/findings/{fid}/close")
assert status("Z-07") == "Close-out" and bt.one("SELECT hp1 FROM zones WHERE id='Z-07'")["hp1"]
upload("/m/Z-07/photo", S + "painted.png")
post7 = bt.one("SELECT * FROM photos WHERE zone_id='Z-07' AND kind='post'")
pc = bt.q("SELECT * FROM candidates WHERE photo_id=?", post7["id"])
c.post(f"/review/{post7['id']}", data={"pick": [r["id"] for r in pc if r["score"] >= 0.6], "note": "녹 번짐",
                                       "rc": [r["id"] for r in pc], "reason": ["기타"] * len(pc)})
assert bt.recur(post7) and "같은 자리 재발" in c.get("/zones/Z-07").text
f7 = bt.recur(post7)[0][1]["id"]
c.post(f"/m/findings/{f7}/action", data={"note": "보수 도장"})
upload(f"/m/findings/{f7}/recheck", S + "painted_fixed.png")
c.post(f"/findings/{f7}/close")
assert status("Z-07") == "Release 완료" and bt.one("SELECT hp2 FROM zones WHERE id='Z-07'")["hp2"]
assert "검사 끝" in c.get("/m/Z-07").text  # 끝난 구역은 폰에 빈 화면 대신 안내

# 프론트 연동 Case JSON: 구조는 고정, 진행에 따라 available과 값만 채워진다
cs = c.get("/api/zones/Z-07/case").json()
assert cs["location"] == {"block_id": "B12P", "zone_id": "Z07", "location_id": "B12P-Z07"}
assert (cs["stage"], cs["case_status"], cs["inspector_decision"]) == ("COATING_INSPECTION", "CLOSED", "PASS")
assert cs["surface_ai"]["label"] == "RECHECK" and cs["registration"]["status"] == "SUCCESS"
assert {p["kind"] for p in cs["photos"]} == {"pre", "recheck", "post"} and cs["recurrences"]
c3 = c.get("/api/zones/Z-06/case").json()  # Close-out(예시 구역): HP1 해제, 도장 차례
assert (c3["stage"], c3["case_status"], c3["inspector_decision"]) == ("PAINTING", "OPEN", "PASS")
c19 = c.get("/api/zones/Z-19/case").json()  # 재검사 대기 (예시 구역)
assert (c19["stage"], c19["case_status"], c19["inspector_decision"]) == ("REINSPECTION", "REINSPECTION", "REPAIR")
c31 = c.get("/api/zones/Z-31/case").json()
assert c31["surface_ai"] == {"available": False, "label": None, "score": None} and not c31["registration"]["available"]
assert c31.keys() == cs.keys() and c.get("/api/zones/Z-99/case").status_code == 404

# 사진 출처: 시연용 예시 사진은 다시 압축돼도 합성, 새로 찍은 사진은 BT_ORIGIN(기본 시편)
assert {p["origin"] for p in bt.q("SELECT origin FROM photos")} == {"합성"}
import cv2  # noqa: E402
from PIL import Image  # noqa: E402
jpg = io.BytesIO()
Image.open(S + "before.png").convert("RGB").resize((600, 292)).save(jpg, "JPEG", quality=60)
c.post("/m/Z-14/photo", files={"photo": ("kakao.jpg", jpg.getvalue(), "image/jpeg")})
ok, flip = cv2.imencode(".png", cv2.flip(cv2.imread(S + "before.png"), 1))
c.post("/m/Z-15/photo", files={"photo": ("plate.png", flip.tobytes(), "image/png")})
assert [bt.one("SELECT origin FROM photos WHERE zone_id=?", z)["origin"] for z in ("Z-14", "Z-15")] == ["합성", "시편"]
assert "라벨 출처" in c.get("/data").text and "origin" in zipfile.ZipFile(io.BytesIO(c.get("/data/export.zip").content)).read("labels.csv").decode("utf-8-sig")

# 효과 분석: 상우 공수 모델 v2/v2.1과 같은 숫자(기본값), 앱 기록 값은 시편·현장 사진만 센다
from server import effect  # noqa: E402
e = effect.report({"tau_app": 0})  # 앱 입력(새로 생긴 일)을 빼면 상우 모델과 같은 값
assert [round(e["T"][m]) for m in ("M0", "M1", "M2")] == [1134, 853, 809] and round(e["eta01"], 1) == 24.8
assert [round(e["risk"][m]) for m in ("M0", "M1", "M2")] == [29, 26, 17] and e["n_dft"] == 248
assert round(e["plus"]["T"]) == 716 and round(e["plus_ai"]["T"]) == 673 and round(e["saved"]["판정"]) == 0
e = effect.report()  # 기본: 앱 입력 2분 × (홀드포인트 1 + 재검 라운드마다 2명)이 더해진다
assert round(e["T"]["M1"]) == 856 and round(e["per"]["M1"][9], 1) == 3.3 and e["per"]["M0"][9] == 0
assert bt.measured()["pi1"]["n"] == 0  # 지금까지 판정한 사진은 전부 합성이라 세지 않는다
c.post("/m/Z-30/photo", files={"photo": ("plate.png", flip.tobytes(), "image/png")})  # 좌우 뒤집은 사진 = 시편, 도장 전
p15 = bt.one("SELECT id FROM photos WHERE zone_id='Z-30'")["id"]
k15 = [r["id"] for r in bt.q("SELECT id FROM candidates WHERE photo_id=?", p15)]
c.post(f"/review/{p15}", data={"pick": k15, "note": "잔류 녹", "rc": k15, "reason": ["기타"] * len(k15)})
m15 = bt.measured()
assert (m15["pi1"]["v"], m15["pi1"]["n"], m15["n_punch"]["v"]) == (1.0, 1, len(k15))
ep = c.get("/effect").text
assert "판정 시간은 0분" in ep and "18.9" in ep and "c-close\">계산에 씀" not in ep  # 표본 1건은 아직 기본값을 쓴다
assert "<svg class=\"wf\"" in ep and "숫자의 근거" in ep and "실측 <b>0</b>" in ep and "앱 입력" in ep

# 품질 분석: 지도(판정한 기록이 있는 구역만 세고 나머지는 '기록 없음') · 도막 두께 90/10
assert bt.zone_q("Z-04")[:3:2] == (3, 2) and bt.zone_q("Z-18") is None  # Z-04: 지적 3 · 재발 1 + 반려했던 자리 1
qh = c.get("/quality").text  # 품질 분석 첫 화면 = 블록 고르기 (블록마다 판정 기록 요약)
assert all(f'href="/quality/{b}"' in qh for b in ("B12P", "B12S")) and "90/10 미충족" in qh
qp = c.get("/quality/B12P?m=nf").text
assert "품질 분석" in qp and '<a class="qn" href="/zones/Z-18"' in qp and '<b class="mono">Z-04</b> 3건' in qp
assert "같은 자리 기준" in c.get("/quality/B12P?m=rc").text and c.get("/quality/B13P").status_code == 404
# 블록·면을 골라 들어가고(단면 링크는 지도 기준 m을 유지), 검사 현황 ↔ 품질 분석을 같은 블록·면으로 오간다
qs = c.get("/quality/B12S?s=H&m=rw").text
assert 'href="/zones/H-31"' in qs and 'href="/quality/B12S?s=D&amp;m=rw"' in qs and 'href="/quality/B12P?s=H&amp;m=rw"' in qs
assert 'href="/blocks/B12S?s=H"' in qs and 'href="/quality/B12S?s=H"' in c.get("/blocks/B12S?s=H").text
assert 'href="/blocks/B12P?s=Z">← 전체 평면도' in c.get("/zones/Z-04").text  # 구역 화면 → 그 면 전체 평면도
assert c.get("/quality/B13P").status_code == 404 and c.get("/quality/B12P?s=X").status_code == 404
dq = c.get("/quality/B12P?m=dft").text
assert "90/10 계산 결과 · 미충족" in dq and "최종 판정은 검사원" in dq and '<a class="qbad" href="/zones/Z-04"' in dq and "87.5%" in dq  # 예시: Z-04 보수 전 320 미만 3점 / 24점
c.post("/quality/dft", data={"text": "Z-04, 341\nz-04, 352\nZ-04 330\nZ-04, 300\nZ-99, 300\n구역,µm"})
assert [r["um"] for r in bt.q("SELECT um FROM dft WHERE zone_id='Z-04'")] == [341, 352, 330, 300]
dq = c.get("/quality/B12P?m=dft").text  # 300은 288~320이라 10% 안에서 허용: 19/20 = 95%, 최소 300 → 충족, 테두리 없음
assert "90/10 계산 결과 · 충족" in dq and "미충족" not in dq and '<a class="qbad"' not in dq and "도막 두께 4점 입력" in c.get("/zones/Z-04").text

# 같은 자리 기준: 중심점. 옮겨 온 사각형을 스치기만 하면 같은 자리가 아니다
sq = [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]]
assert bt.overlaps(sq, {"x0": 0.25, "y0": 0.25, "x1": 0.35, "y1": 0.35})
assert not bt.overlaps(sq, {"x0": 0.38, "y0": 0.38, "x1": 0.6, "y1": 0.6})

# 폰: 카메라 / 사진첩 버튼이 따로 있다
mz = c.get("/m/Z-20").text
assert "사진첩에서 고르기" in mz and mz.count('name="photo"') == 2

# 사진이 아닌 파일은 거절
r = c.post("/m/Z-21/photo", files={"photo": ("a.txt", b"hello", "text/plain")})
assert r.status_code == 400

print(f"OK  재검사 정합 대응점 {rc['matches']}개 · 오차 {err:.4f} / 도장 후 정합 {link['matches']}개 · 오차 {err2:.4f} / 도장 후 재검사 {rc4['matches']}개 · 오차 {err4:.4f}")

# 시연 초기화: 서버를 끄지 않고 시작 상태로 (기록·올린 사진을 지우고 예시 데이터로 다시)
c.post("/demo/reset")
assert status("Z-07") == "검사 대기" and status("Z-04") == "조치 완료" and not any(bt.UPLOADS.iterdir())
assert bt.one("SELECT COUNT(*) n FROM zones")["n"] == 4 * 48 and "시연 초기화" in c.get("/").text
