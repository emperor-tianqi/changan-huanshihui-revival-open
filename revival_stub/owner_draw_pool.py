"""The deterministic 妖灵 test pool behind the Owner draw-test overlay.

D-147 pauses probability/RNG, so the overlay must never *select* a card by
weight.  It does still need more than one card: a fixture that always shows
the same 葱油包 is not a usable manual-play fixture.  This module freezes a
**test pool** taken from the shipped client card table
(`apk_unpacked/assets/config/card/card.max`, CN 0.1.20.1): every card with
star 1-4, food ids 1-5 excluded, one entry per character name (the lowest
star variant), sorted by card id.

This is **not** the game's real draw pool and it carries no weights or
pity.  Selection is a fixed-stride rotation over the frozen tuple
(`serial * POOL_STRIDE + index`) so ten results are distinct and consecutive
draws vary.  Every emitted row keeps the deterministic-fixture provenance
(`deterministic_test_fixture_not_rng`, `real_probability=false`).

`max_decoder/analyze_draw_pool.py` re-derives the same tuple from the APK
and prints `POOL_DIGEST`; when `apk_unpacked/` is absent the frozen table
still drives the tests, exactly like the other recovered constants.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class DrawPoolCard:
    """One frozen test-pool entry (id/star/sfm ids/name from the client table)."""

    card_id: int
    star: int
    sfm_id1: int
    sfm_id2: int
    name: str


POOL_SOURCE = (
    "apk_unpacked/assets/config/card/card.max CN 0.1.20.1: "
    "star 1-4, food ids 1-5 excluded, unique character names, lowest star"
)

#: Fixed stride, coprime with the pool size.  Deterministic, not random.
POOL_STRIDE = 53

POOL_CARDS = (
    DrawPoolCard(1101, 1, 30479, 30261, "葱油包"),
    DrawPoolCard(2201, 2, 30103, 30146, "贪食鬼"),
    DrawPoolCard(3301, 3, 10059, 30028, "铁拳"),
    DrawPoolCard(4401, 4, 13008, 30226, "机关师"),
    DrawPoolCard(4402, 4, 30268, 30150, "风生兽"),
    DrawPoolCard(4403, 4, 30118, 30309, "疾龙猎手"),
    DrawPoolCard(4404, 4, 20009, 36111, "青丘之女"),
    DrawPoolCard(2202, 2, 10010, 30210, "木武者"),
    DrawPoolCard(3302, 3, 13011, 30265, "撞撞龙"),
    DrawPoolCard(4405, 4, 10009, 30268, "地灵巨人"),
    DrawPoolCard(4406, 4, 30151, 30039, "邙山鬼母"),
    DrawPoolCard(4407, 4, 13009, 30425, "梦魇"),
    DrawPoolCard(2203, 2, 30115, 30203, "神机营"),
    DrawPoolCard(4408, 4, 13006, 30475, "年兽"),
    DrawPoolCard(3305, 3, 30402, 30471, "湘夫人"),
    DrawPoolCard(4409, 4, 30456, 30373, "蛇女"),
    DrawPoolCard(4410, 4, 15011, 30098, "灵芝神侍"),
    DrawPoolCard(2204, 2, 10057, 30262, "狸猫小僧"),
    DrawPoolCard(4411, 4, 13001, 30096, "八刃魔童"),
    DrawPoolCard(4412, 4, 13018, 30213, "罗刹女"),
    DrawPoolCard(3307, 3, 30233, 30037, "诅咒娃娃"),
    DrawPoolCard(4413, 4, 30405, 13017, "银角"),
    DrawPoolCard(4414, 4, 10045, 30268, "铁角龙"),
    DrawPoolCard(1102, 1, 25003, 30208, "吓人箱"),
    DrawPoolCard(2205, 2, 30480, 30273, "扫晴娘"),
    DrawPoolCard(4415, 4, 30395, 30107, "金角"),
    DrawPoolCard(4416, 4, 20023, 30030, "风师"),
    DrawPoolCard(3308, 3, 30415, 10046, "白蛇郎君"),
    DrawPoolCard(4417, 4, 30603, 30032, "禅武僧"),
    DrawPoolCard(2206, 2, 10055, 30273, "神箭鸮"),
    DrawPoolCard(4418, 4, 10017, 25010, "龙吉公主"),
    DrawPoolCard(4419, 4, 30465, 20006, "洛神"),
    DrawPoolCard(4420, 4, 23006, 30039, "孟婆"),
    DrawPoolCard(2207, 2, 30229, 30274, "藤二郎"),
    DrawPoolCard(3309, 3, 15008, 30072, "黑翼血蝠"),
    DrawPoolCard(4421, 4, 30323, 30107, "骸骨狼王"),
    DrawPoolCard(4422, 4, 30456, 13023, "玉兔"),
    DrawPoolCard(4423, 4, 30143, 30053, "荒古神子"),
    DrawPoolCard(4424, 4, 23009, 30444, "邪音魔罗"),
    DrawPoolCard(2208, 2, 20008, 30349, "呱太爷"),
    DrawPoolCard(3310, 3, 30150, 30211, "六刃蛛"),
    DrawPoolCard(4425, 4, 30132, 30118, "血翅迦楼罗"),
    DrawPoolCard(4426, 4, 30107, 30132, "血滴子"),
    DrawPoolCard(4427, 4, 13005, 30099, "影舞者"),
    DrawPoolCard(1103, 1, 30104, 30144, "奎木狼"),
    DrawPoolCard(2209, 2, 30451, 30210, "狐仙"),
    DrawPoolCard(4428, 4, 37096, 30477, "玄甲傀儡"),
    DrawPoolCard(3311, 3, 10058, 30264, "象力士"),
    DrawPoolCard(4429, 4, 30602, 30149, "狂刀"),
    DrawPoolCard(4430, 4, 23008, 30404, "骷髅禅师"),
    DrawPoolCard(2211, 2, 20005, 30460, "花灵"),
    DrawPoolCard(4431, 4, 30659, 30623, "疾光白虎"),
    DrawPoolCard(4432, 4, 30710, 30017, "酒熊猫"),
    DrawPoolCard(3312, 3, 10092, 30277, "琉璃盏"),
    DrawPoolCard(4433, 4, 30150, 30110, "人偶师"),
    DrawPoolCard(4434, 4, 20022, 30039, "白骨夫人"),
    DrawPoolCard(2212, 2, 30071, 30104, "符草人"),
    DrawPoolCard(4435, 4, 30615, 30073, "魅影"),
    DrawPoolCard(4436, 4, 30716, 30717, "幽冥铁骑"),
    DrawPoolCard(3313, 3, 30454, 30328, "神灯老者"),
    DrawPoolCard(4437, 4, 13013, 30269, "青鸾"),
    DrawPoolCard(2213, 2, 30415, 30037, "毒蛛"),
    DrawPoolCard(4438, 4, 30647, 37095, "天罡神将"),
    DrawPoolCard(4439, 4, 30720, 30204, "天狼星·破军"),
    DrawPoolCard(4440, 4, 30627, 30108, "魔武者"),
    DrawPoolCard(1104, 1, 30449, 30208, "草灵"),
    DrawPoolCard(2214, 2, 15010, 30072, "伞先生"),
    DrawPoolCard(3314, 3, 30052, 30106, "石巨人"),
    DrawPoolCard(4441, 4, 30721, 30342, "火女"),
    DrawPoolCard(4442, 4, 30764, 30655, "天狼星·修罗"),
    DrawPoolCard(4443, 4, 10081, 30203, "沙行鸟"),
    DrawPoolCard(4444, 4, 20002, 30192, "心月狐"),
    DrawPoolCard(2215, 2, 10072, 30037, "巫毒娃娃"),
    DrawPoolCard(3315, 3, 30105, 30445, "吠火"),
    DrawPoolCard(4445, 4, 30118, 30126, "青岩白鹿"),
    DrawPoolCard(4446, 4, 10069, 30288, "迦摩"),
    DrawPoolCard(4447, 4, 10086, 20022, "曼陀罗"),
    DrawPoolCard(2216, 2, 30381, 30037, "扫把头"),
    DrawPoolCard(4448, 4, 23004, 30426, "怨灵"),
    DrawPoolCard(3317, 3, 10060, 30393, "炎煞"),
    DrawPoolCard(4449, 4, 10067, 30052, "虎力士"),
    DrawPoolCard(4501, 4, 30821, 30476, "剑公子"),
    DrawPoolCard(2217, 2, 30443, 30137, "穿山力士"),
    DrawPoolCard(4502, 4, 30121, 30310, "飞廉"),
    DrawPoolCard(4503, 4, 10041, 36115, "武圣"),
    DrawPoolCard(3318, 3, 30117, 30309, "飞羽客"),
    DrawPoolCard(4504, 4, 36116, 30032, "赤帝"),
    DrawPoolCard(4505, 4, 13024, 25011, "广寒仙子"),
    DrawPoolCard(1105, 1, 30146, 30101, "引魂灯"),
    DrawPoolCard(2218, 2, 30511, 30149, "山豹"),
    DrawPoolCard(4506, 4, 10011, 30098, "琉璃仙"),
    DrawPoolCard(4507, 4, 23003, 30713, "九婴"),
    DrawPoolCard(3319, 3, 10046, 30266, "雪衣郎将"),
    DrawPoolCard(4508, 4, 23007, 30701, "九尾天狐"),
    DrawPoolCard(2219, 2, 10047, 30274, "小石人"),
    DrawPoolCard(4509, 4, 36117, 36118, "狩魂使"),
    DrawPoolCard(4510, 4, 30386, 30152, "黄泉灵女"),
    DrawPoolCard(4511, 4, 30718, 30719, "酆都君"),
    DrawPoolCard(2220, 2, 30401, 10088, "夜游神"),
    DrawPoolCard(3320, 3, 20008, 30349, "丹朱青羽"),
    DrawPoolCard(4512, 4, 25008, 30053, "重明鸟"),
    DrawPoolCard(4513, 4, 10036, 30226, "太古魔像"),
    DrawPoolCard(4514, 4, 30712, 30250, "白衣圣子"),
    DrawPoolCard(4515, 4, 10110, 36004, "墨麒麟"),
    DrawPoolCard(2221, 2, 30445, 30262, "油锅小鬼"),
    DrawPoolCard(3321, 3, 30211, 30453, "雨师妾"),
    DrawPoolCard(4516, 4, 10005, 30097, "妖猴大圣·火"),
    DrawPoolCard(4517, 4, 30397, 30143, "女魃"),
    DrawPoolCard(4518, 4, 10006, 36120, "罗睺"),
    DrawPoolCard(1107, 1, 30103, 30261, "火夜叉"),
    DrawPoolCard(2222, 2, 30148, 30532, "巫篁"),
    DrawPoolCard(4519, 4, 23010, 30476, "夜摩天"),
    DrawPoolCard(3322, 3, 10084, 30098, "冰川天女"),
    DrawPoolCard(4520, 4, 36119, 30216, "魔猿大将"),
    DrawPoolCard(4521, 4, 36131, 30311, "妖猴大圣·阴"),
    DrawPoolCard(2223, 2, 30262, 30181, "肥球"),
    DrawPoolCard(4522, 4, 36133, 36134, "妖猴大圣·林"),
    DrawPoolCard(4523, 4, 36186, 36187, "影煞"),
    DrawPoolCard(3326, 3, 10046, 30483, "铁兵卫"),
    DrawPoolCard(4524, 4, 30323, 30152, "虚影"),
    DrawPoolCard(4525, 4, 27005, 37091, "饕餮"),
    DrawPoolCard(2224, 2, 30210, 30104, "火焰角"),
    DrawPoolCard(4526, 4, 37101, 37103, "罗罗"),
    DrawPoolCard(4527, 4, 37105, 37106, "三娘"),
    DrawPoolCard(3327, 3, 30138, 30146, "阿修罗"),
    DrawPoolCard(4528, 4, 27006, 37110, "江鱼儿"),
    DrawPoolCard(2225, 2, 10059, 30262, "幻影"),
    DrawPoolCard(4529, 4, 37120, 37121, "判官"),
    DrawPoolCard(4530, 4, 37130, 37131, "天狼星·汐风"),
    DrawPoolCard(4531, 4, 37140, 37141, "炎煌"),
)
def pool_digest(cards=POOL_CARDS) -> str:
    """sha256 over the canonical tuple; the analyzer prints the same value."""

    canonical = "|".join(
        f"{card.card_id}:{card.star}:{card.sfm_id1}:{card.sfm_id2}:{card.name}"
        for card in cards
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


POOL_DIGEST = pool_digest()


def pool_index_at(serial: int, index: int) -> int:
    """The deterministic pool slot for one result of one draw request."""

    if not POOL_CARDS:
        raise ValueError("empty draw test pool")
    return (int(serial) * POOL_STRIDE + int(index)) % len(POOL_CARDS)


def pool_card_at(serial: int, index: int) -> DrawPoolCard:
    """Deterministic card for one result slot of one draw request.

    `serial` is the overlay's per-request fixture serial (1-based) and
    `index` the 0-based result slot.  The fixed stride keeps a ten-draw
    distinct and makes consecutive draws continue through the pool; no
    random source is involved.
    """

    return POOL_CARDS[pool_index_at(serial, index)]


def pool_card_ids() -> tuple[int, ...]:
    """The frozen ids, for tests that must not depend on the APK tree."""

    return tuple(card.card_id for card in POOL_CARDS)

