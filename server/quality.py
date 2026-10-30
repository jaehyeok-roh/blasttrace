"""촬영 품질 확인 (담당: 재혁)."""
import cv2
import numpy as np

# 조명 부족: 전체가 어둡고(밝기 중간값) 가장 밝은 곳조차 어두우면(상위 1%) 다시 찍게 한다.
# 평균만 보면 원래 짙은 블라스팅 면(시연 사진 중간값 54)까지 걸러서 둘을 같이 본다.
# 시편 112장 + 시연 사진 4장으로 정한 값: 흰 조명 상위 1% 199 이상, 어두운 조명 188 이하, 시연 사진 171~204(중간값 54~135).
# ponytail: 여유가 4~7로 좁다. 현장 폰 사진이 쌓이면 다시 맞춘다
DARK_MID, DARK_TOP = 85, 195


def check_quality(image_path: str) -> dict:
    """반환: {"glare": 반사로 하얗게 날아간 화소 비율, "blur": 선명도(라플라시안 분산), "dark": 조명 부족, "ok": 통과 여부}"""
    img = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {"glare": 1.0, "blur": 0.0, "dark": False, "ok": False}
    s = min(1.0, 640 / max(img.shape[:2]))
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    glare = float((img.min(axis=2) > 240).mean())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mid, top = np.percentile(gray, (50, 99))
    dark = bool(mid < DARK_MID and top < DARK_TOP)
    # ponytail: 흐림은 표시만 하고 판정에 안 쓴다. 폰 사진으로 기준값을 잡은 뒤 추가
    return {"glare": round(glare, 4), "blur": round(blur, 1), "dark": dark, "ok": glare < 0.03 and not dark}
