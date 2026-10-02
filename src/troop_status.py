from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import time
from typing import Optional

import cv2
import numpy as np
import debug_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORLD_MAP_REFERENCE = PROJECT_ROOT / "screenshots" / "templates" / "world_map_reference.png"
# 菜单和回基地按钮不随剩余兵力变化；只匹配按钮内部，避开海水背景。
WORLD_MAP_ANCHORS = ((12, 104, 52, 48), (1190, 630, 66, 67))
WORLD_MAP_MIN_SCORE = 0.95


@lru_cache(maxsize=1)
def _world_map_templates():
    reference = cv2.imdecode(np.fromfile(str(WORLD_MAP_REFERENCE), dtype=np.uint8), cv2.IMREAD_COLOR)
    if reference is None or reference.shape != (720, 1280, 3):
        raise ValueError(f"世界地图参考图无效：{WORLD_MAP_REFERENCE}")
    return tuple(reference[y:y+h, x:x+w].copy() for x, y, w, h in WORLD_MAP_ANCHORS)


def is_world_map_visible(screen):
    """使用固定界面按钮判断地图，不依赖合并显示的兵种卡片数量。"""
    if screen is None or screen.shape != (720, 1280, 3):
        return False, []
    scores = []
    for (x, y, w, h), template in zip(WORLD_MAP_ANCHORS, _world_map_templates()):
        region = screen[y-5:y+h+5, x-5:x+w+5]
        # 平方差同时比较亮度，避免把被弹窗压暗的地图误认为可点击地图。
        difference = cv2.matchTemplate(region, template, cv2.TM_SQDIFF_NORMED)
        scores.append(1.0 - float(cv2.minMaxLoc(difference)[0]))
    return all(score >= WORLD_MAP_MIN_SCORE for score in scores), scores

# 1280x720 下左下角兵种栏。只比较 8 个登陆艇卡片，排除英雄和右侧按钮。
TROOP_BAR_REGION = (0, 640, 416, 80)
TROOP_SLOT_WIDTH = 52
EXPECTED_TROOP_SLOTS = 8
SIGNATURE_SLOT_SIZE = (52, 36)
MAX_MEAN_DIFF_FOR_SAME = 7.0
MAX_SLOT_DIFF_FOR_SAME = 14.0
TROOP_BAR_MIN_BRIGHT_RATIO = 0.25
TROOP_BAR_MIN_VISIBLE_SLOTS = 6


@dataclass
class TroopSnapshot:
    slot_count: int
    signature: np.ndarray
    debug_path: Optional[Path] = None

    @property
    def valid(self):
        return self.slot_count > 0 and self.signature.size > 0


def crop_region(image, region):
    if image is None or image.ndim != 3:
        raise ValueError("兵力快照收到的截图无效。")
    x, y, width, height = region
    if x < 0 or y < 0 or x + width > image.shape[1] or y + height > image.shape[0]:
        raise ValueError(f"兵力区域超出截图范围：region={region}, image={image.shape[:2]}")
    return image[y : y + height, x : x + width]


def estimate_slot_count(crop):
    count = 0
    for slot_index in range(EXPECTED_TROOP_SLOTS):
        x = slot_index * TROOP_SLOT_WIDTH
        slot = crop[:, x : x + TROOP_SLOT_WIDTH]
        if slot.size == 0:
            continue

        gray = cv2.cvtColor(slot, cv2.COLOR_BGR2GRAY)
        texture = float(gray.std())
        if texture > 8:
            count += 1
    return count


def is_troop_bar_visible(screen):
    crop = crop_region(screen, TROOP_BAR_REGION)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    visible_slots = 0
    ratios = []
    for slot_index in range(EXPECTED_TROOP_SLOTS):
        x = slot_index * TROOP_SLOT_WIDTH
        slot = hsv[:, x : x + TROOP_SLOT_WIDTH]
        bright_card = cv2.inRange(
            slot,
            np.array([0, 0, 150], dtype=np.uint8),
            np.array([179, 100, 255], dtype=np.uint8),
        )
        ratio = float(cv2.countNonZero(bright_card)) / max(1, bright_card.size)
        ratios.append(ratio)
        if ratio >= TROOP_BAR_MIN_BRIGHT_RATIO:
            visible_slots += 1
    return visible_slots >= TROOP_BAR_MIN_VISIBLE_SLOTS, ratios


def build_signature(crop):
    slot_signatures = []
    for slot_index in range(EXPECTED_TROOP_SLOTS):
        x = slot_index * TROOP_SLOT_WIDTH
        slot = crop[:36, x : x + TROOP_SLOT_WIDTH]
        if slot.shape[:2] != (36, TROOP_SLOT_WIDTH):
            continue
        gray = cv2.cvtColor(slot, cv2.COLOR_BGR2GRAY)
        normalized = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        normalized = cv2.resize(normalized, SIGNATURE_SLOT_SIZE, interpolation=cv2.INTER_AREA)
        slot_signatures.append(normalized)

    if not slot_signatures:
        return np.empty((0, 0), dtype=np.float32)
    return np.concatenate(slot_signatures, axis=1).astype(np.float32)


def save_debug(crop, label):
    if not debug_settings.SAVE_ERROR_SCREENSHOTS:
        return None
    output_dir = PROJECT_ROOT / "screenshots" / "errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"troop_status_{label}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imencode(".png", crop)[1].tofile(str(output_path))
    return output_path


def capture(screen, label=None):
    crop = crop_region(screen, TROOP_BAR_REGION)
    snapshot = TroopSnapshot(
        slot_count=estimate_slot_count(crop),
        signature=build_signature(crop),
        debug_path=save_debug(crop, label) if label else None,
    )
    print(f"兵力快照：slot_count={snapshot.slot_count}, debug={snapshot.debug_path}")
    return snapshot


def is_same(before, after):
    if before is None or after is None:
        return False
    if not before.valid or not after.valid:
        return False
    if before.slot_count != after.slot_count:
        print(f"兵种数量变化：before={before.slot_count}, after={after.slot_count}")
        return False

    if before.signature.shape != after.signature.shape:
        print(f"兵力签名尺寸变化：before={before.signature.shape}, after={after.signature.shape}")
        return False

    absolute_diff = np.abs(before.signature - after.signature)
    mean_diff = float(np.mean(absolute_diff))
    slot_width = SIGNATURE_SLOT_SIZE[0]
    slot_diffs = [
        float(np.mean(absolute_diff[:, index * slot_width : (index + 1) * slot_width]))
        for index in range(EXPECTED_TROOP_SLOTS)
    ]
    max_slot_diff = max(slot_diffs, default=float("inf"))
    print(
        f"兵力快照差异：mean={mean_diff:.3f}, max_slot={max_slot_diff:.3f}, "
        f"slots={[round(value, 2) for value in slot_diffs]}"
    )
    return mean_diff <= MAX_MEAN_DIFF_FOR_SAME and max_slot_diff <= MAX_SLOT_DIFF_FOR_SAME
