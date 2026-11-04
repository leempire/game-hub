"""Chinese four-character Xiangqi notation from the pre-move board."""

_NAMES = {"K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
          "k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒"}
_NUMBERS = "零一二三四五六七八九"


def notation(board: list, frm, to) -> str:
    fr, fc = frm
    tr, tc = to
    piece = board[fr][fc]
    if piece not in _NAMES:
        raise ValueError("起点没有象棋棋子")
    red = piece.isupper()
    number = lambda n: _NUMBERS[n] if red else str(n)
    file_number = lambda col: 9 - col if red else col + 1
    peers = sorted((r for r, row in enumerate(board) if row[fc] == piece), reverse=not red)
    prefix = _NAMES[piece] + number(file_number(fc))
    if len(peers) >= 2 and piece.upper() not in ("A", "B"):
        index = peers.index(fr)
        position = ("前" if index == 0 else "后" if index == len(peers) - 1 else
                    "中" if len(peers) == 3 else _NUMBERS[index + 1])
        # Two stacked pawn files would otherwise both have a 'front pawn'.
        stacked_files = sum(sum(row[col] == piece for row in board) >= 2 for col in range(9))
        label = number(file_number(fc)) if piece.upper() == "P" and stacked_files > 1 else _NAMES[piece]
        prefix = position + label
    direction = "平" if fr == tr else "进" if (tr < fr) == red else "退"
    destination = file_number(tc) if fr == tr or piece.upper() in ("N", "A", "B") else abs(tr - fr)
    return prefix + direction + number(destination)
