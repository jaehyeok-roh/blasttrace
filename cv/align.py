"""전후 사진 정합 (담당: 동희). 지금은 예선 실험에서 쓴 SIFT + RANSAC이 실제로 돈다.
SuperPoint + LightGlue로 바꿀 때 함수 이름과 돌려주는 모양만 지키면 된다. 예시: shared/align.json"""
import cv2
import numpy as np


def _read(path, max_side=1280):
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, 1.0
    s = min(1.0, max_side / max(img.shape))
    return (cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img), s


def sane(H, w, h) -> bool:
    """정합 행렬이 사진으로 말이 되는가: 거울상이 아니고, 배율 0.25~4배, 옮긴 네 모서리가 볼록한 사각형.
    어두운 시편 사진에서 대응점 35~154개로 사진을 점 하나로 찌그러뜨리는 행렬(배율 0.001~0.03)이 '성공'으로 나온 적이 있다"""
    H = np.asarray(H, float)
    det = np.linalg.det(H[:2, :2] / H[2, 2])
    if det <= 0 or not 0.25 < np.sqrt(det) < 4:
        return False
    p = cv2.perspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), H)
    return bool(np.isfinite(p).all() and cv2.isContourConvex(p))


def align(before_path: str, after_path: str) -> dict:
    """조치 전 사진 -> 재검사 사진 변환.
    반환: {"success": bool, "matches": RANSAC 통과 대응점 수, "homography": 3x3 (원본 픽셀 기준) 또는 None}"""
    (a, sa), (b, sb) = _read(before_path), _read(after_path)
    fail = {"success": False, "matches": 0, "homography": None}
    if a is None or b is None:
        return fail
    sift = cv2.SIFT_create(4000)
    k1, d1 = sift.detectAndCompute(a, None)
    k2, d2 = sift.detectAndCompute(b, None)
    if d1 is None or d2 is None:
        return fail
    good = [p[0] for p in cv2.BFMatcher().knnMatch(d1, d2, k=2)
            if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return fail
    src = np.float32([k1[g.queryIdx].pt for g in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[g.trainIdx].pt for g in good]).reshape(-1, 1, 2)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    n = int(inl.sum()) if inl is not None else 0
    if H is None or n < 30:  # 서로 다른 사진끼리는 대응점이 0~1개였다 (예선 실험)
        return {**fail, "matches": n}
    # 줄인 사진 기준 변환을 원본 픽셀 기준으로 되돌린다
    H = np.diag([1 / sb, 1 / sb, 1]) @ H @ np.diag([sa, sa, 1])
    if not sane(H, a.shape[1] / sa, a.shape[0] / sa):
        return {**fail, "matches": n}
    return {"success": True, "matches": n, "homography": (H / H[2, 2]).round(6).tolist()}
