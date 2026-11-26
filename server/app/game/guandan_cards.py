"""Guandan card strength, wildcard interpretation, and legal selections."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import permutations


RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = "SHDC"
JOKERS = ("SJ", "BJ")
_NATURAL = {rank: index for index, rank in enumerate(RANKS + JOKERS)}
_SEQUENCES = ("straight", "straight_flush", "tube", "plate")


def is_wild(card, level):
    return card["rank"] == level and card["suit"] == "H"


def strength(rank, level):
    return len(RANKS) if rank == level else _NATURAL[rank] + (rank in JOKERS)


@dataclass(frozen=True)
class _Pattern:
    kind: str
    rank: str
    size: int
    groups: tuple[tuple[str, int], ...]
    suit: str = ""


def _patterns():
    for kind, size, ranks in (("single", 1, RANKS + JOKERS), ("pair", 2, RANKS + JOKERS),
                              ("triple", 3, RANKS)):
        for rank in ranks:
            yield _Pattern(kind, rank, size, ((rank, size),))
    for triple, pair in permutations(RANKS + JOKERS, 2):
        if triple not in JOKERS:
            yield _Pattern("full_house", triple, 5, ((triple, 3), (pair, 2)))
    ace_low = ("A",) + RANKS
    for kind, run, copies in (("straight", 5, 1), ("tube", 3, 2), ("plate", 2, 3)):
        for start in range(len(ace_low) - run + 1):
            ranks = ace_low[start:start + run]
            groups = tuple((rank, copies) for rank in ranks)
            yield _Pattern(kind, ranks[-1], run * copies, groups)
            if kind == "straight":
                for suit in SUITS:
                    yield _Pattern("straight_flush", ranks[-1], 5, groups, suit)
    for size in range(4, 11):
        for rank in RANKS:
            yield _Pattern("bomb", rank, size, ((rank, size),))
    yield _Pattern("joker_bomb", "BJ", 4, (("SJ", 2), ("BJ", 2)))


_PATTERNS = tuple(_patterns())
_BY_SIZE = {size: tuple(p for p in _PATTERNS if p.size == size) for size in range(1, 11)}


def _index(cards, level):
    by_face = defaultdict(list)
    wilds = []
    for card in sorted(cards, key=lambda card: card["id"]):
        if is_wild(card, level):
            wilds.append(card)
        else:
            by_face[(card["rank"], "")].append(card)
            if card["suit"]:
                by_face[(card["rank"], card["suit"])].append(card)
    return by_face, wilds


def _fit(pattern, by_face, wilds):
    cards, replacements = [], []
    wild_index = 0
    for rank, count in pattern.groups:
        natural = by_face.get((rank, pattern.suit), [])[:count]
        missing = count - len(natural)
        if missing > len(wilds) - wild_index or (missing and rank in JOKERS):
            return None
        cards.extend(natural)
        for card in wilds[wild_index:wild_index + missing]:
            suit = pattern.suit or card["suit"]
            if (rank, suit) != (card["rank"], card["suit"]):
                if pattern.kind == "single":
                    return None
                replacements.append({"id": card["id"], "rank": rank, "suit": suit})
            cards.append(card)
        wild_index += missing
    return {"kind": pattern.kind, "rank": pattern.rank, "cards": cards, "wild": replacements}


def _power(kind, rank, size, level):
    tier = 0
    if kind == "joker_bomb":
        tier = 12
    elif kind == "straight_flush":
        tier = 6
    elif kind == "bomb":
        tier = size + (size >= 6)
    value = _NATURAL[rank] if kind in _SEQUENCES else strength(rank, level)
    return tier, value


def _combo_power(combo, level):
    return _power(combo["kind"], combo["rank"], len(combo["cards"]), level)


def _beats(kind, power, previous_kind, previous_power):
    return (power[0] > 0 or kind == previous_kind) and power > previous_power


def resolve(cards, level, previous):
    by_face, wilds = _index(cards, level)
    options = []
    for pattern in _BY_SIZE.get(len(cards), ()):
        if combo := _fit(pattern, by_face, wilds):
            options.append((combo, _combo_power(combo, level)))
    if not options:
        raise ValueError("选中的牌不能组成合法牌型")
    if previous is None:
        return max(options, key=lambda option: (option[1][0], option[0]["kind"] == "plate", option[1][1]))[0]
    previous_kind = previous["kind"]
    previous_power = _combo_power(previous, level)
    legal = [option for option in options if _beats(option[0]["kind"], option[1], previous_kind, previous_power)]
    if not legal:
        raise ValueError("选中的牌需要能压过上一手牌")
    return max(legal, key=lambda option: (option[0]["kind"] == previous_kind, option[1]))[0]


def suggest(hand, level, previous):
    if previous is None:
        try:
            return resolve(hand, level, None)
        except ValueError:
            card = min(hand, key=lambda c: (strength(c["rank"], level), is_wild(c, level), c["id"]))
            return resolve([card], level, None)

    previous_kind = previous["kind"]
    previous_power = _combo_power(previous, level)
    by_face, wilds = _index(hand, level)
    # Testing one selection per rank template bounds work even with all 27 cards.
    options = []
    for pattern in _PATTERNS:
        if pattern.size > len(hand):
            continue
        power = _power(pattern.kind, pattern.rank, pattern.size, level)
        if _beats(pattern.kind, power, previous_kind, previous_power):
            options.append((pattern.kind != previous_kind, power, pattern))
    for _, _, pattern in sorted(options, key=lambda option: option[:2]):
        if combo := _fit(pattern, by_face, wilds):
            return resolve(combo["cards"], level, previous)
    return None


def opening_candidates(hand, level):
    """Sample ordinary leads using the same rank templates as the validator."""
    by_face, wilds = _index(hand, level)
    seen = set()
    for pattern in _PATTERNS:
        if pattern.size > len(hand) or _power(pattern.kind, pattern.rank, pattern.size, level)[0]:
            continue
        fitted = _fit(pattern, by_face, wilds)
        if fitted is None:
            continue
        ids = frozenset(card["id"] for card in fitted["cards"])
        if ids in seen:
            continue
        seen.add(ids)
        combo = resolve(fitted["cards"], level, None)
        if not _combo_power(combo, level)[0]:
            yield combo
