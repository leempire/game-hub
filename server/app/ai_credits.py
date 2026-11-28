"""Exact AI accounting. One displayed point = 250,000 integer units."""

from decimal import Decimal

from app.database import get_conn
from app.ai_pricing import (
    CREDIT_UNITS as UNITS, CACHED_INPUT_UNITS, UNCACHED_INPUT_UNITS, OUTPUT_UNITS,
)



def balance_units(user_id):
    conn = get_conn()
    try:
        row = conn.execute('SELECT ai_credit_units FROM users WHERE id=?', (user_id,)).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def set_balance(user_id, points, admin_id):
    amount = Decimal(str(points)) * UNITS
    if not amount.is_finite() or amount != amount.to_integral_value() or abs(amount) > 10**15:
        raise ValueError('积分数值无效，最多保留 6 位小数且须为 0.000004 的整数倍')
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT ai_credit_units FROM users WHERE id=?', (user_id,)).fetchone()
        if row is None:
            raise LookupError('用户不存在')
        conn.execute('UPDATE users SET ai_credit_units=? WHERE id=?', (int(amount), user_id))
        conn.execute('''INSERT INTO ai_credit_adjustments(admin_id,user_id,previous_units,new_units)
                        VALUES (?,?,?,?)''', (admin_id, user_id, row[0], int(amount)))
        conn.commit()
    finally:
        conn.close()


def begin_call(operation_id, user_id, model):
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT ai_credit_units FROM users WHERE id=?', (user_id,)).fetchone()
        if row is None or row[0] < 0:
            raise ValueError('房主 AI 积分不足，已停止 AI 调用')
        cursor = conn.execute('''INSERT OR IGNORE INTO ai_usage(operation_id,user_id,model,status)
                                 VALUES (?,?,?,'pending')''', (operation_id, user_id, model))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def token_cost(usage):
    prompt, output = usage['prompt_tokens'], usage['completion_tokens']
    cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
    if any(type(n) is not int or n < 0 for n in (prompt, output, cached)) or cached > prompt:
        raise ValueError('AI 返回的 token 用量无效')
    cost = cached * CACHED_INPUT_UNITS + (prompt - cached) * UNCACHED_INPUT_UNITS + output * OUTPUT_UNITS
    return cached, prompt - cached, output, cost


def settle_call(operation_id, usage):
    cached, uncached, output, cost = token_cost(usage)
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM ai_usage WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            raise ValueError('AI 调用未登记')
        if row['status'] == 'settled':
            return
        conn.execute('UPDATE users SET ai_credit_units=ai_credit_units-? WHERE id=?',
                     (cost, row['user_id']))
        conn.execute('''UPDATE ai_usage SET status='settled',cached_tokens=?,uncached_tokens=?,
                        output_tokens=?,cost_units=? WHERE operation_id=?''',
                     (cached, uncached, output, cost, operation_id))
        conn.commit()
    finally:
        conn.close()


def mark_unknown(operation_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE ai_usage SET status='unknown' WHERE operation_id=? AND status='pending'",
                     (operation_id,))
        conn.commit()
    finally:
        conn.close()
