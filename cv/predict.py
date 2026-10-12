"""RGB 후보 표시 (담당: 동희). 지금은 임시 규칙: 주변보다 뚜렷하게 주황·갈색인 얼룩(녹)을 찾는다.
학습 모델로 바꿀 때 함수 이름과 돌려주는 모양만 지키면 서버는 고칠 필요가 없다. 예시: shared/predict.json"""
import cv2
import numpy as np

MODEL_VERSION = "rust-color-v0"  # 학습 모델로 바꾸면 "resnet18-v1"처럼 바꾼다


def predict(image_path: str) -> dict:
    """다시 볼 곳 후보. 좌표는 원본 사진 픽셀 기준 (x, y는 왼쪽 위 꼭짓점).
    반환: {"candidates": [{"x", "y", "w", "h", "score"}, ...], "model_version": str}
    score는 얼룩 안쪽과 바로 바깥의 채도 차이를 0~1로 옮긴 값이다. 정답 확률이 아니다."""
    img = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
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
    return {"candidates": sorted(cands, key=lambda c: -c["score"])[:5], "model_version": MODEL_VERSION}
