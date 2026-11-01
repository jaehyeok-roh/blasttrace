"""RGB 후보 표시 (담당: 동희). 실제 사진은 YOLO11n-seg(동희 학습, 시편 S02~S04 84장), 합성 예시 사진은 색 규칙을 쓴다.
모델이 합성 사진은 85%를 표시해서 시연 예시에는 쓸 수 없다. 가중치(cv/weights, git에 안 올림)나 ultralytics가 없는 PC는 색 규칙으로 돈다.
함수 이름과 돌려주는 모양만 지키면 서버는 고칠 필요가 없다. 예시: shared/predict.json"""
import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

COLOR_VERSION = "rust-color-v0"
YOLO_VERSION = "yolo11n-seg-v1"
WEIGHTS = Path(os.environ.get("BT_WEIGHTS", Path(__file__).parent / "weights" / "best_rust_seg_aug.pt"))
CONF = 0.25  # 동희 평가: 0.25에서 Precision 0.85 · Recall 0.69. 더 낮추면 조각만 늘고 위 5개는 거의 그대로다
TOP = 5


@lru_cache(maxsize=1)
def yolo():
    """모델을 한 번만 불러온다. 쓸 수 없으면 None"""
    if not WEIGHTS.exists():
        return None
    try:
        from ultralytics import YOLO
    except ImportError:
        return None
    return YOLO(str(WEIGHTS))


def regions(score, w, h):
    """score = 칸마다 확신도(0이면 표시 없음)인 저해상도 지도, w·h = 원본 크기. YOLO는 사진 한 장에 조각을 50~100개 내서
    그대로는 후보 카드로 못 쓴다. 가까운 조각을 묶어 큰 순이 아니라 확신도 순으로 TOP개를 돌려준다"""
    mh, mw = score.shape
    k = max(3, int(mw * 0.02))
    n, lab = cv2.connectedComponents(cv2.dilate((score > 0).astype(np.uint8), np.ones((k, k), np.uint8)))
    out = []
    for i in range(1, n):
        part = (lab == i) & (score > 0)
        if part.sum() < 0.002 * mh * mw:  # 점 같은 조각은 버린다
            continue
        ys, xs = np.where(part)
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        out.append({"x": int(x0 / mw * w), "y": int(y0 / mh * h), "w": int((x1 - x0) / mw * w), "h": int((y1 - y0) / mh * h),
                    "score": round(float(score[part].max()), 2)})
    return sorted(out, key=lambda c: -c["score"])[:TOP]


def by_model(model, img):
    r = model.predict(img, conf=CONF, imgsz=640, verbose=False, device="cpu")[0]
    if r.masks is None:
        return {"candidates": [], "model_version": YOLO_VERSION}
    masks = r.masks.data.cpu().numpy() > 0.5
    score = np.zeros(masks.shape[1:], float)
    for mask, conf in zip(masks, r.boxes.conf.cpu().numpy()):
        score[mask] = np.maximum(score[mask], conf)
    return {"candidates": regions(score, img.shape[1], img.shape[0]), "model_version": YOLO_VERSION}


def predict(image_path: str, real: bool = True) -> dict:
    """다시 볼 곳 후보. 좌표는 원본 사진 픽셀 기준 (x, y는 왼쪽 위 꼭짓점). real=False = 합성 예시 사진(색 규칙).
    반환: {"candidates": [{"x", "y", "w", "h", "score"}, ...], "model_version": str}
    score는 YOLO 확신도 또는 색 규칙의 채도 차이(0~1)다. 정답 확률이 아니다."""
    img = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    model = yolo() if real else None
    return by_model(model, img) if model is not None else by_color(img)


def by_color(img):
    """주변보다 뚜렷하게 주황·갈색인 얼룩(녹)을 찾는 임시 규칙"""
    s = min(1.0, 800 / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    # ponytail: 색 규칙 하나뿐. 녹 색 범위(색상 4~15, 채도 110 이상, 밝기 60~135)는 예시 사진 기준이라 실제 시편으로 다시 맞춘다
    rust = cv2.inRange(hsv, (4, 110, 60), (15, 255, 135))
    rust = cv2.morphologyEx(rust, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    blobs = cv2.morphologyEx(rust, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(blobs)
    h, w = small.shape[:2]
    cands = []
    for i, (x, y, bw, bh, area) in enumerate(st.tolist()):
        if i == 0 or area < 0.002 * h * w:
            continue
        blob = lab == i
        inner = blob & (rust > 0)
        ring = cv2.dilate(blob.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool) & ~blob
        if not inner.any() or not ring.any():
            continue
        contrast = hsv[..., 1][inner].mean() - hsv[..., 1][ring].mean()
        if contrast < 50:  # 녹은 70~97, 갈색 띠는 10~44였다. 주변도 비슷하게 주황이면 녹이 아니라 원래 표면 색 (예시 사진의 갈색 띠)
            continue
        cands.append({"x": int(x / s), "y": int(y / s), "w": int(bw / s), "h": int(bh / s),
                      "score": round(min(contrast / 100, 0.99), 2)})
    return {"candidates": sorted(cands, key=lambda c: -c["score"])[:TOP], "model_version": COLOR_VERSION}
