"""Frozen card growth tables (CN 0.1.20.1).

The recovered client builds these tables from shipped config chunks
(``pkgCfgCardMgr.init``):

* ``CARD_STAR_RARE`` -- ``card.max.star`` plus ``templete.rare`` for all 350
  card config ids that have a handbook template;
* ``LEVEL_EXP`` / ``DOG_FOOD_EXP`` -- ``cardLv.max`` rows: the per-level EXP the
  client charges and the EXP one dog-food card of that star is worth;
* ``SPLIT_REWARDS`` -- ``cardSplit.max`` keyed exactly as
  ``pkgCfgCardMgr.getCardSplitCfg(star, rare, advanceLevel)`` looks it up;
* ``SPECIAL_CARD_STARS`` -- the five dog-food special-card rows' own stars
  (``specialCard.max``, ids 1-5);
* ``ADVANCE_COSTS`` -- ``cardAdvance.max`` rows keyed exactly as
  ``pkgCfgAdvanceCardMgr.getAdvanceCardSD(star, rare, level)`` looks them up
  (TASK-0206: the card-advance wallet sink).

``max_decoder/analyze_card_growth.py`` re-derives the same tables from the APK
and prints ``CARD_GROWTH_DIGEST``; the digest below is computed from the frozen
tables, so a client change cannot silently move a level cost or a split reward.
``max_decoder/analyze_card_advance.py`` does the same for ``ADVANCE_COSTS`` and
prints ``ADVANCE_COST_DIGEST``.

The star -> max level rule is the client's own: ``getMaxtLevel`` returns 5 for
stars 1-2 and 10 for star 3 and above.
"""

from __future__ import annotations

import hashlib

#: sha256 of the canonical table lines; the analyzer prints the same value.
CARD_GROWTH_DIGEST = "83bcbd4a60d4e1ac087bce830f1a020ed1b7903e3129b265d939f702e191d6c5"

#: sha256 of ``ADVANCE_COSTS`` + ``ADVANCE_MAX_LEVEL_BY_STAR``; the
#: ``analyze_card_advance.py`` analyzer re-derives the same value from the APK.
ADVANCE_COST_DIGEST = "fad9c7d67470871631ce4dd4e9faa4c75da623bd54f5034bc1bd256aab0dc7c2"

#: Dog-food special-card config ids (``FOOD_CARD_LIST`` in pkgCardConstMgr).
DOG_FOOD_CARD_IDS = (1, 2, 3, 4, 5)

#: The client divides at level 5 for 1-2 star cards and level 10 otherwise.
SECOND_STRENGTH_LEVEL = 5
MAX_STRENGTH_LEVEL = 10

CARD_STAR_RARE: dict[int, tuple[int, float]] = {
    1101: (1, 1.0),
    1102: (1, 1.0),
    1103: (1, 1.0),
    1104: (1, 1.0),
    1105: (1, 1.0),
    1107: (1, 1.0),
    2201: (2, 2.0),
    2202: (2, 2.0),
    2203: (2, 2.0),
    2204: (2, 2.0),
    2205: (2, 2.0),
    2206: (2, 2.0),
    2207: (2, 2.0),
    2208: (2, 2.0),
    2209: (2, 2.0),
    2211: (2, 2.0),
    2212: (2, 2.0),
    2213: (2, 2.0),
    2214: (2, 2.0),
    2215: (2, 2.0),
    2216: (2, 2.0),
    2217: (2, 2.0),
    2218: (2, 2.0),
    2219: (2, 2.0),
    2220: (2, 2.0),
    2221: (2, 2.0),
    2222: (2, 2.0),
    2223: (2, 2.0),
    2224: (2, 2.0),
    2225: (2, 2.0),
    3301: (3, 3.0),
    3302: (3, 3.0),
    3305: (3, 3.0),
    3307: (3, 3.0),
    3308: (3, 3.0),
    3309: (3, 3.0),
    3310: (3, 3.0),
    3311: (3, 3.0),
    3312: (3, 3.0),
    3313: (3, 3.0),
    3314: (3, 3.0),
    3315: (3, 3.0),
    3317: (3, 3.0),
    3318: (3, 3.0),
    3319: (3, 3.0),
    3320: (3, 3.0),
    3321: (3, 3.0),
    3322: (3, 3.0),
    3326: (3, 3.0),
    3327: (3, 3.0),
    4301: (4, 3.0),
    4302: (4, 3.0),
    4305: (4, 3.0),
    4307: (4, 3.0),
    4308: (4, 3.0),
    4309: (4, 3.0),
    4310: (4, 3.0),
    4311: (4, 3.0),
    4312: (4, 3.0),
    4313: (4, 3.0),
    4314: (4, 3.0),
    4315: (4, 3.0),
    4317: (4, 3.0),
    4318: (4, 3.0),
    4319: (4, 3.0),
    4320: (4, 3.0),
    4321: (4, 3.0),
    4322: (4, 3.0),
    4326: (4, 3.0),
    4327: (4, 3.0),
    4401: (4, 4.1),
    4402: (4, 4.0),
    4403: (4, 4.0),
    4404: (4, 4.1),
    4405: (4, 4.0),
    4406: (4, 4.1),
    4407: (4, 4.0),
    4408: (4, 4.0),
    4409: (4, 4.0),
    4410: (4, 4.1),
    4411: (4, 4.0),
    4412: (4, 4.0),
    4413: (4, 4.1),
    4414: (4, 4.0),
    4415: (4, 4.0),
    4416: (4, 4.1),
    4417: (4, 4.1),
    4418: (4, 4.1),
    4419: (4, 4.1),
    4420: (4, 4.1),
    4421: (4, 4.1),
    4422: (4, 4.1),
    4423: (4, 4.1),
    4424: (4, 4.1),
    4425: (4, 4.1),
    4426: (4, 4.2),
    4427: (4, 4.2),
    4428: (4, 4.2),
    4429: (4, 4.2),
    4430: (4, 4.2),
    4431: (4, 4.2),
    4432: (4, 4.2),
    4433: (4, 4.2),
    4434: (4, 4.2),
    4435: (4, 4.2),
    4436: (4, 4.2),
    4437: (4, 4.2),
    4438: (4, 4.2),
    4439: (4, 4.2),
    4440: (4, 4.2),
    4441: (4, 4.2),
    4442: (4, 4.2),
    4443: (4, 4.0),
    4444: (4, 4.0),
    4445: (4, 4.0),
    4446: (4, 4.0),
    4447: (4, 4.0),
    4448: (4, 4.0),
    4449: (4, 4.0),
    4501: (4, 5.0),
    4502: (4, 5.0),
    4503: (4, 5.0),
    4504: (4, 5.0),
    4505: (4, 5.0),
    4506: (4, 5.0),
    4507: (4, 5.0),
    4508: (4, 5.0),
    4509: (4, 5.0),
    4510: (4, 5.0),
    4511: (4, 5.0),
    4512: (4, 5.0),
    4513: (4, 5.0),
    4514: (4, 5.0),
    4515: (4, 5.0),
    4516: (4, 5.0),
    4517: (4, 5.0),
    4518: (4, 5.0),
    4519: (4, 5.0),
    4520: (4, 5.0),
    4521: (4, 5.0),
    4522: (4, 5.0),
    4523: (4, 5.0),
    4524: (4, 5.0),
    4525: (4, 5.0),
    4526: (4, 5.0),
    4527: (4, 5.0),
    4528: (4, 5.0),
    4529: (4, 5.0),
    4530: (4, 5.0),
    4531: (4, 5.0),
    5301: (5, 3.0),
    5302: (5, 3.0),
    5305: (5, 3.0),
    5307: (5, 3.0),
    5308: (5, 3.0),
    5309: (5, 3.0),
    5310: (5, 3.0),
    5311: (5, 3.0),
    5312: (5, 3.0),
    5313: (5, 3.0),
    5314: (5, 3.0),
    5315: (5, 3.0),
    5317: (5, 3.0),
    5318: (5, 3.0),
    5319: (5, 3.0),
    5320: (5, 3.0),
    5321: (5, 3.0),
    5322: (5, 3.0),
    5326: (5, 3.0),
    5327: (5, 3.0),
    5401: (5, 4.1),
    5402: (5, 4.0),
    5403: (5, 4.0),
    5404: (5, 4.1),
    5405: (5, 4.0),
    5406: (5, 4.1),
    5407: (5, 4.0),
    5408: (5, 4.0),
    5409: (5, 4.0),
    5410: (5, 4.1),
    5411: (5, 4.0),
    5412: (5, 4.0),
    5413: (5, 4.1),
    5414: (5, 4.0),
    5415: (5, 4.0),
    5416: (5, 4.1),
    5417: (5, 4.1),
    5418: (5, 4.1),
    5419: (5, 4.1),
    5420: (5, 4.1),
    5421: (5, 4.1),
    5422: (5, 4.1),
    5423: (5, 4.1),
    5424: (5, 4.1),
    5425: (5, 4.1),
    5426: (5, 4.2),
    5427: (5, 4.2),
    5428: (5, 4.2),
    5429: (5, 4.2),
    5430: (5, 4.2),
    5431: (5, 4.2),
    5432: (5, 4.2),
    5433: (5, 4.2),
    5434: (5, 4.2),
    5435: (5, 4.2),
    5436: (5, 4.2),
    5437: (5, 4.2),
    5438: (5, 4.2),
    5439: (5, 4.2),
    5440: (5, 4.2),
    5441: (5, 4.2),
    5442: (5, 4.2),
    5443: (5, 4.0),
    5444: (5, 4.0),
    5445: (5, 4.0),
    5446: (5, 4.0),
    5447: (5, 4.0),
    5448: (5, 4.0),
    5449: (5, 4.0),
    5501: (5, 5.0),
    5502: (5, 5.0),
    5503: (5, 5.0),
    5504: (5, 5.0),
    5505: (5, 5.0),
    5506: (5, 5.0),
    5507: (5, 5.0),
    5508: (5, 5.0),
    5509: (5, 5.0),
    5510: (5, 5.0),
    5511: (5, 5.0),
    5512: (5, 5.0),
    5513: (5, 5.0),
    5514: (5, 5.0),
    5515: (5, 5.0),
    5516: (5, 5.0),
    5517: (5, 5.0),
    5518: (5, 5.0),
    5519: (5, 5.0),
    5520: (5, 5.0),
    5521: (5, 5.0),
    5522: (5, 5.0),
    5523: (5, 5.0),
    5524: (5, 5.0),
    5525: (5, 5.0),
    5526: (5, 5.0),
    5527: (5, 5.0),
    5528: (5, 5.0),
    5529: (5, 5.0),
    5530: (5, 5.0),
    5531: (5, 5.0),
    6301: (6, 3.0),
    6302: (6, 3.0),
    6305: (6, 3.0),
    6307: (6, 3.0),
    6308: (6, 3.0),
    6309: (6, 3.0),
    6310: (6, 3.0),
    6311: (6, 3.0),
    6312: (6, 3.0),
    6313: (6, 3.0),
    6314: (6, 3.0),
    6315: (6, 3.0),
    6317: (6, 3.0),
    6318: (6, 3.0),
    6319: (6, 3.0),
    6320: (6, 3.0),
    6321: (6, 3.0),
    6322: (6, 3.0),
    6326: (6, 3.0),
    6327: (6, 3.0),
    6401: (6, 4.1),
    6402: (6, 4.0),
    6403: (6, 4.0),
    6404: (6, 4.1),
    6405: (6, 4.0),
    6406: (6, 4.1),
    6407: (6, 4.0),
    6408: (6, 4.0),
    6409: (6, 4.0),
    6410: (6, 4.1),
    6411: (6, 4.0),
    6412: (6, 4.0),
    6413: (6, 4.1),
    6414: (6, 4.0),
    6415: (6, 4.0),
    6416: (6, 4.1),
    6417: (6, 4.1),
    6418: (6, 4.1),
    6419: (6, 4.1),
    6420: (6, 4.1),
    6421: (6, 4.1),
    6422: (6, 4.1),
    6423: (6, 4.1),
    6424: (6, 4.1),
    6425: (6, 4.1),
    6426: (6, 4.2),
    6427: (6, 4.2),
    6428: (6, 4.2),
    6429: (6, 4.2),
    6430: (6, 4.2),
    6431: (6, 4.2),
    6432: (6, 4.2),
    6433: (6, 4.2),
    6434: (6, 4.2),
    6435: (6, 4.2),
    6436: (6, 4.2),
    6437: (6, 4.2),
    6438: (6, 4.2),
    6439: (6, 4.2),
    6440: (6, 4.2),
    6441: (6, 4.2),
    6442: (6, 4.2),
    6443: (6, 4.0),
    6444: (6, 4.0),
    6445: (6, 4.0),
    6446: (6, 4.0),
    6447: (6, 4.0),
    6448: (6, 4.0),
    6449: (6, 4.0),
    6501: (6, 5.0),
    6502: (6, 5.0),
    6503: (6, 5.0),
    6504: (6, 5.0),
    6505: (6, 5.0),
    6506: (6, 5.0),
    6507: (6, 5.0),
    6508: (6, 5.0),
    6509: (6, 5.0),
    6510: (6, 5.0),
    6511: (6, 5.0),
    6512: (6, 5.0),
    6513: (6, 5.0),
    6514: (6, 5.0),
    6515: (6, 5.0),
    6516: (6, 5.0),
    6517: (6, 5.0),
    6518: (6, 5.0),
    6519: (6, 5.0),
    6520: (6, 5.0),
    6521: (6, 5.0),
    6522: (6, 5.0),
    6523: (6, 5.0),
    6524: (6, 5.0),
    6525: (6, 5.0),
    6526: (6, 5.0),
    6527: (6, 5.0),
    6528: (6, 5.0),
    6529: (6, 5.0),
    6530: (6, 5.0),
    6531: (6, 5.0),
}

LEVEL_EXP: dict[int, tuple[int, ...]] = {
    1: (1, 2, 5, 6, 7, 8, 10, 12, 14, 16, 18),
    2: (1, 2, 5, 6, 7, 8, 10, 12, 14, 16, 18),
    3: (2, 4, 5, 6, 7, 8, 20, 24, 28, 32, 36),
    4: (4, 8, 10, 12, 14, 16, 40, 48, 56, 64, 72),
    5: (16, 32, 40, 48, 56, 64, 160, 192, 224, 256, 288),
    6: (32, 64, 80, 96, 112, 128, 320, 384, 448, 512, 576),
}

DOG_FOOD_EXP: dict[int, int] = {
    1: 5,
    2: 10,
    3: 20,
    4: 50,
    5: 100,
    6: 200,
}

SPLIT_REWARDS: dict[tuple[int, float, int], tuple[int, int]] = {
    (1, 1.0, 0): (5, 0),
    (2, 2.0, 0): (36, 0),
    (3, 3.0, 0): (180, 36),
    (3, 3.0, 1): (230, 36),
    (3, 3.0, 2): (330, 36),
    (3, 3.0, 3): (480, 36),
    (4, 3.0, 0): (540, 108),
    (4, 3.0, 1): (1140, 228),
    (4, 3.0, 2): (2140, 428),
    (4, 3.0, 3): (3740, 748),
    (4, 4.0, 0): (540, 108),
    (4, 4.0, 1): (1140, 228),
    (4, 4.0, 2): (2140, 428),
    (4, 4.0, 3): (3740, 748),
    (4, 4.1, 0): (1080, 216),
    (4, 4.1, 1): (1980, 396),
    (4, 4.1, 2): (3480, 696),
    (4, 4.1, 3): (5880, 1176),
    (4, 4.2, 0): (1080, 216),
    (4, 4.2, 1): (1980, 396),
    (4, 4.2, 2): (3480, 696),
    (4, 4.2, 3): (5880, 1176),
    (4, 5.0, 0): (2160, 432),
    (4, 5.0, 1): (3360, 672),
    (4, 5.0, 2): (5360, 1072),
    (4, 5.0, 3): (8560, 1712),
    (5, 3.0, 0): (4280, 856),
    (5, 3.0, 1): (6680, 1336),
    (5, 3.0, 2): (10680, 2136),
    (5, 3.0, 3): (16280, 3256),
    (5, 4.0, 0): (4280, 856),
    (5, 4.0, 1): (6680, 1336),
    (5, 4.0, 2): (10680, 2136),
    (5, 4.0, 3): (16280, 3256),
    (5, 4.1, 0): (6960, 1392),
    (5, 4.1, 1): (9360, 1872),
    (5, 4.1, 2): (13360, 2672),
    (5, 4.1, 3): (18960, 3792),
    (5, 4.2, 0): (6960, 1392),
    (5, 4.2, 1): (9360, 1872),
    (5, 4.2, 2): (13360, 2672),
    (5, 4.2, 3): (18960, 3792),
    (5, 5.0, 0): (10720, 2144),
    (5, 5.0, 1): (13120, 2624),
    (5, 5.0, 2): (17120, 3424),
    (5, 5.0, 3): (22720, 4544),
    (6, 3.0, 0): (20560, 4112),
    (6, 4.0, 0): (20560, 4112),
    (6, 4.1, 0): (25920, 5184),
    (6, 4.2, 0): (25920, 5184),
    (6, 5.0, 0): (33440, 6688),
}

SPECIAL_CARD_STARS: dict[int, int] = {
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
}

#: ``cardAdvance.max`` exactly as ``pkgCfgAdvanceCardMgr.init`` stores it:
#: ``le_tb_card_advance[star][rare][targetLevel]``.  The client's own filter
#: (``star > 0 and level > 0 and rare > 0``) drops the shipped star-6 placeholder
#: row, so neither it nor any unnamed value enters the lookup table.  The tuple
#: is ``(money, yinpo, jinpo, silverPiece, goldPiece, cardNum)`` -- the exact
#: fields ``pkgAdvanceCardMgr.checkMaterial`` charges.
ADVANCE_COSTS: dict[tuple[int, float, int], tuple[int, int, int, int, int, int]] = {
    (3, 3.0, 1): (200, 50, 0, 0, 0, 0),
    (3, 3.0, 2): (300, 100, 0, 0, 0, 0),
    (3, 3.0, 3): (400, 150, 0, 0, 0, 0),
    (3, 3.0, 4): (600, 0, 0, 0, 0, 1),
    (4, 3.0, 1): (300, 600, 120, 0, 0, 0),
    (4, 3.0, 2): (400, 1000, 200, 0, 0, 0),
    (4, 3.0, 3): (500, 1600, 320, 0, 0, 0),
    (4, 3.0, 4): (800, 0, 0, 4, 0, 1),
    (4, 4.0, 1): (300, 600, 120, 0, 0, 0),
    (4, 4.0, 2): (400, 1000, 200, 0, 0, 0),
    (4, 4.0, 3): (500, 1600, 320, 0, 0, 0),
    (4, 4.0, 4): (800, 0, 0, 4, 0, 1),
    (4, 4.1, 1): (300, 900, 180, 0, 0, 0),
    (4, 4.1, 2): (400, 1500, 300, 0, 0, 0),
    (4, 4.1, 3): (500, 2400, 480, 0, 0, 0),
    (4, 4.1, 4): (800, 0, 0, 6, 0, 1),
    (4, 4.2, 1): (300, 900, 180, 0, 0, 0),
    (4, 4.2, 2): (400, 1500, 300, 0, 0, 0),
    (4, 4.2, 3): (500, 2400, 480, 0, 0, 0),
    (4, 4.2, 4): (800, 0, 0, 6, 0, 1),
    (4, 5.0, 1): (300, 1200, 240, 0, 0, 0),
    (4, 5.0, 2): (400, 2000, 400, 0, 0, 0),
    (4, 5.0, 3): (500, 3200, 640, 0, 0, 0),
    (4, 5.0, 4): (800, 0, 0, 8, 0, 1),
    (5, 3.0, 1): (400, 2400, 480, 6, 4, 0),
    (5, 3.0, 2): (500, 4000, 800, 8, 6, 0),
    (5, 3.0, 3): (600, 5600, 1120, 10, 8, 0),
    (5, 3.0, 4): (1000, 0, 0, 0, 10, 1),
    (5, 4.0, 1): (400, 2400, 480, 6, 4, 0),
    (5, 4.0, 2): (500, 4000, 800, 8, 6, 0),
    (5, 4.0, 3): (600, 5600, 1120, 10, 8, 0),
    (5, 4.0, 4): (1000, 0, 0, 0, 10, 1),
    (5, 4.1, 1): (400, 2400, 480, 6, 4, 0),
    (5, 4.1, 2): (500, 4000, 800, 8, 6, 0),
    (5, 4.1, 3): (600, 5600, 1120, 10, 8, 0),
    (5, 4.1, 4): (1000, 0, 0, 0, 10, 1),
    (5, 4.2, 1): (400, 2400, 480, 6, 4, 0),
    (5, 4.2, 2): (500, 4000, 800, 8, 6, 0),
    (5, 4.2, 3): (600, 5600, 1120, 10, 8, 0),
    (5, 4.2, 4): (1000, 0, 0, 0, 10, 1),
    (5, 5.0, 1): (400, 2400, 480, 6, 4, 0),
    (5, 5.0, 2): (500, 4000, 800, 8, 6, 0),
    (5, 5.0, 3): (600, 5600, 1120, 10, 8, 0),
    (5, 5.0, 4): (1000, 0, 0, 0, 10, 1),
}

#: ``pkgCfgAdvanceCardMgr.getMaxAdvLevel``: the largest ``level`` per star among
#: the rows above.  Star 6 keeps the shipped placeholder's 0, and the client's
#: own ``isShowAdvanceBtn`` gate (``3 <= star < 6``) is what actually admits a
#: card, so no star outside 3-5 can be advanced even if a row existed.
ADVANCE_MAX_LEVEL_BY_STAR: dict[int, int] = {3: 4, 4: 4, 5: 4, 6: 0}

#: ``pkgAdvanceCardConst.isShowAdvanceBtn``: only stars 3, 4 and 5 show the
#: advance button, and ``pkgAdvanceCardConst.checkAdvance`` additionally demands
#: ``level == MAX_STRENGTH_LEVEL`` (10) and ``advanceLevel < maxAdvLevel``.
ADVANCE_STAR_MIN = 3
ADVANCE_STAR_MAX_EXCLUSIVE = 6


def max_level(star: int) -> int:
    """``pkgCardConstMgr.getMaxtLevel``: 5 for star 1-2, else 10."""

    return SECOND_STRENGTH_LEVEL if int(star) <= 2 else MAX_STRENGTH_LEVEL


def card_star_rare(card_cfg_id: int) -> tuple[int, float]:
    """The shipped ``(star, rare)`` of one card config id."""

    try:
        return CARD_STAR_RARE[int(card_cfg_id)]
    except KeyError as exc:
        raise KeyError(
            f"card config id {card_cfg_id!r} has no card/templete row"
        ) from exc


def dog_food_exp(star: int) -> int:
    """EXP one dog-food card of ``star`` is worth (``cardLv.dogexp``)."""

    return DOG_FOOD_EXP[int(star)]


def dog_food_exp_for_cfg(cfg_id: int) -> int:
    """EXP one dog-food SPECIAL-CARD config id is worth."""

    return dog_food_exp(SPECIAL_CARD_STARS[int(cfg_id)])


def sum_exp(star: int, level: int, exp: int) -> int:
    """The client's ``dSumExp``: every charged level plus progress in this one."""

    table = LEVEL_EXP[int(star)]
    return sum(table[index] for index in range(0, int(level) + 1)) + int(exp)


def calculate_level(star: int, total_sum_exp: int) -> tuple[int, int]:
    """``pkgStrengthCardInfoMgr.calculateLevel`` for one star.

    Returns ``(level, leftover_exp)``.  The loop walks ``exp0..expMaxLevel`` and
    stops at the first level whose cost the pool cannot pay; level 0's cost is
    charged but does not increment the level counter, exactly as the client.
    """

    table = LEVEL_EXP[int(star)]
    maximum = max_level(star)
    level = 0
    remaining = int(total_sum_exp)
    for index in range(0, maximum + 1):
        cost = table[index]
        if remaining - cost < 0:
            break
        if index != 0:
            level += 1
        remaining -= cost
    return level, remaining


def split_reward(star: int, rare: float, advance_level: int) -> tuple[int, int]:
    """``(yinpo, jinpo)`` for one split card, or raise when the config lacks it."""

    try:
        return SPLIT_REWARDS[(int(star), float(rare), int(advance_level))]
    except KeyError as exc:
        raise KeyError(
            f"cardSplit has no row for star={star} rare={rare} "
            f"advanceLevel={advance_level}"
        ) from exc


def split_reward_for_card(card_cfg_id: int, advance_level: int) -> tuple[int, int]:
    """The split reward of one card row, keyed by its shipped star/rare."""

    star, rare = card_star_rare(card_cfg_id)
    return split_reward(star, rare, advance_level)


def advance_max_level(star: int) -> int:
    """``pkgCfgAdvanceCardMgr.getMaxAdvLevel``; 0 when the star has no rows."""

    return ADVANCE_MAX_LEVEL_BY_STAR.get(int(star), 0)


def advance_cost(star: int, rare: float, target_level: int) -> tuple[int, int, int, int, int, int]:
    """The shipped ``(money, yinpo, jinpo, silverPiece, goldPiece, cardNum)``.

    Raises ``KeyError`` when the shipped table has no row for this exact
    ``(star, rare, targetLevel)``, which the client's own
    ``getAdvanceCardSD`` treats as "cannot advance yet".  Callers must refuse
    rather than invent a cost.
    """

    try:
        return ADVANCE_COSTS[(int(star), float(rare), int(target_level))]
    except KeyError as exc:
        raise KeyError(
            f"cardAdvance has no row for star={star} rare={rare} "
            f"level={target_level}"
        ) from exc


def advance_digest() -> str:
    """Recompute ``ADVANCE_COST_DIGEST`` from the frozen table."""

    canonical: list[str] = []
    for (star, rare, level), costs in sorted(ADVANCE_COSTS.items()):
        joined = ",".join(str(value) for value in costs)
        canonical.append(f"advance:{star}:{rare!r}:{level}:{joined}")
    for star, level in sorted(ADVANCE_MAX_LEVEL_BY_STAR.items()):
        canonical.append(f"advance-max:{star}:{level}")
    return hashlib.sha256("|".join(canonical).encode("utf-8")).hexdigest()


def frozen_digest() -> str:
    """Recompute ``CARD_GROWTH_DIGEST`` from the frozen tables."""

    canonical: list[str] = []
    for card_id, (star, rare) in sorted(CARD_STAR_RARE.items()):
        canonical.append(f"card:{card_id}:{star}:{rare!r}")
    for star, exp in sorted(LEVEL_EXP.items()):
        joined = ",".join(str(value) for value in exp)
        canonical.append(f"level:{star}:{joined}:{DOG_FOOD_EXP[star]}")
    for (star, rare, level), (yinpo, jinpo) in sorted(SPLIT_REWARDS.items()):
        canonical.append(f"split:{star}:{rare!r}:{level}:{yinpo}:{jinpo}")
    for cfg_id, star in sorted(SPECIAL_CARD_STARS.items()):
        canonical.append(f"food:{cfg_id}:{star}")
    return hashlib.sha256("|".join(canonical).encode("utf-8")).hexdigest()
