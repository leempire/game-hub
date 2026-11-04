"""Public phrase catalogue shared by room metadata and chat authorization."""

PHRASES = {
    'uno': (
        ('hello', '大家好，一起玩吧'), ('your_turn', '轮到你了'),
        ('nice', '打得漂亮'), ('uno', '别忘了喊 UNO'), ('thanks', '谢谢配合'),
    ),
    'stasis': (
        ('hello', '大家好，一起守住'), ('help', '这边需要支援'),
        ('repair', '请帮忙修门'), ('defend', '先守好舱室'),
        ('resources', '我在攒资源'), ('thanks', '谢谢配合'),
    ),
}


def phrases_for(game: str) -> list[dict]:
    return [{"id": phrase, "text": text} for phrase, text in PHRASES.get(game, ())]
