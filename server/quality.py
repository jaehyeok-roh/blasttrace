"""촬영 품질 확인 (담당: 재혁)."""
import cv2
import numpy as np


def check_quality(image_path: str) -> dict:
    """반환: {"glare": 반사로 하얗게 날아간 화소 비율, "blur": 선명도(라플라시안 분산), "ok": 통과 여부}"""
    img = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {"glare": 1.0, "blur": 0.0, "ok": False}
    s = min(1.0, 640 / max(img.shape[:2]))
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    glare = float((img.min(axis=2) > 240).mean())
    blur = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    # ponytail: 흐림은 표시만 하고 판정에 안 쓴다. 폰 사진으로 기준값을 잡은 뒤 추가
    return {"glare": round(glare, 4), "blur": round(blur, 1), "ok": glare < 0.03}
