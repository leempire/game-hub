"""Negotiated delta transport. Simulation and saved snapshots remain complete."""

ROW_FIELDS = ('enemies', 'loot', 'chests', 'caches', 'effects', 'fields', 'shots')


def row_id(row):
    return row['id'] if isinstance(row, dict) else row[0]


def encode(previous, current):
    changes = {key: value for key, value in current.items()
               if key not in ROW_FIELDS and previous.get(key) != value}
    rows = {}
    for key in ROW_FIELDS:
        before = {row_id(row): row for row in previous[key]}
        after = {row_id(row): row for row in current[key]}
        updated = [row for uid, row in after.items() if before.get(uid) != row]
        removed = [uid for uid in before if uid not in after]
        if updated or removed:
            rows[key] = {'upsert': updated, 'remove': removed}
    return {'type': 'abyss_delta', 'base': previous['frame'], 'game': changes, 'rows': rows}
