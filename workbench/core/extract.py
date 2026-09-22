"""AI 提取模块（可插拔 provider）

- provider = rule : 内置规则引擎，离线、确定性，用于 Demo 与回归测试。
- provider = llm  : OpenAI 兼容 Chat Completions 接口，需在 config/app.json 中启用并配置。

两者输出同一套结构：
    {field_key: {'value':..., 'quote':..., 'confidence':'high|medium|low', 'status':...}}

规则引擎处理的口语难题：
- 中文数字（"三栋""二十万"）
- 总量线索优先（"一共是四栋" 优先于 "两栋是车间"）
- 否定语境（"驻场暂时不需要" 不能判为需要驻场）
- 自我更正（"一共四栋……哦不对，那是五栋" → 取更正后的值并标记待确认）
"""
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime

from .config import app_config, field_config, product_config
from .runtime_env import read_env

CONF_ORDER = {'high': 3, 'medium': 2, 'low': 1}

# 逗号切分只在"后续 25 字内不出现句末标点"时生效，避免把一句完整的话拦腰截断
SENT_SPLIT = re.compile(r'[。！？!?;\n\r]+|(?<=[，,])(?=[^。！？!\n\r]{25,})')
PUNCT_TRIM = ' \t，,。;；:：、"\'（）()【】[]'

NEG_PRE = ['不', '没有', '没', '无需', '不用', '非', '未', '排除']
NEG_POST = ['不需要', '不用', '无需', '不要', '不考虑', '暂时不', '先不']
TOTAL_CUES = ['一共', '总共', '合计', '共计', '总计', '共']

CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


class ExtractionError(Exception):
    pass


# ------------------------------------------------------------------ 基础工具

def split_sentences(text):
    parts = [p.strip() for p in SENT_SPLIT.split(text or '')]
    return [p for p in parts if p and len(p) >= 2]


def trim_punct(s):
    return (s or '').strip(PUNCT_TRIM)


def clean_quote(s, limit=160):
    s = trim_punct(s)
    return s if len(s) <= limit else s[:limit] + '…'


def norm_space(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def cn2num(s):
    """中文数字 → 数值。支持 一 / 十 / 二十 / 三万 / 两 等写法。"""
    if s is None:
        return None
    s = s.strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', s):
        return float(s)
    total, section, num = 0, 0, None
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch == '十':
            section += (num if num else 1) * 10
            num = None
        elif ch == '百':
            section += (num if num else 1) * 100
            num = None
        elif ch == '千':
            section += (num if num else 1) * 1000
            num = None
        elif ch == '万':
            section = ((section + (num or 0)) or 1) * 10000
            total += section
            section, num = 0, None
        else:
            return None
    return float(total + section + (num or 0))


def negated(sentence, kw):
    """该关键词在本句中是否处于否定语境（所有出现位置均被否定）。"""
    found = False
    start = 0
    while True:
        i = sentence.find(kw, start)
        if i < 0:
            break
        found = True
        pre = sentence[max(0, i - 6):i]
        post = sentence[i + len(kw): i + len(kw) + 6]
        pre_neg = any(c in pre for c in NEG_PRE)
        post_neg = any(c in post for c in NEG_POST)
        if not (pre_neg or post_neg):
            return False
        start = i + len(kw)
    return found


def keyword_hits(sentence, keywords, guard=True):
    """返回本句中"处于肯定语境"的关键词数量。guard=False 时忽略否定语境。"""
    hits = 0
    for kw in keywords:
        if kw not in sentence:
            continue
        if (not guard) or (not negated(sentence, kw)):
            hits += 1
    return hits


# ------------------------------------------------------------------ 各模式抽取器

STRIP_LEAD = re.compile(
    r'^(?:我们|他们|你们|对方|客户|业主|甲方|本次)?(?:这边|那边)?(?:的)?'
    r'(?:全称|全名|名字|名称|地址)?(?:是|叫|称为|称作|为|位于|坐落在|坐落于|:|：|\s)+')


def _after_keyword(sentence, kw):
    """取关键词之后的片段，剥掉"是/叫/为/："等引导词。"""
    idx = sentence.find(kw)
    if idx < 0:
        return '', False
    colon = sentence[idx + len(kw):idx + len(kw) + 1] in ('：', ':')
    tail = sentence[idx + len(kw):]
    tail = re.split(r'[，,。;；!！?？]', tail)[0]
    return trim_punct(STRIP_LEAD.sub('', tail)), colon


def _segment_containing(sentence, kw):
    for seg in re.split(r'[，,;；]', sentence):
        if kw in seg:
            return trim_punct(STRIP_LEAD.sub('', seg))
    return ''


def extract_entity(sentences, spec):
    """抽取机构/人名/地址等实体。

    打分规则：冒号引导（"客户全称：XXX"）权重最高；长度适中加分；过长扣分。
    """
    cands = []
    for s in sentences:
        for kw in spec.get('keywords', []):
            if kw in s:
                v, colon = _after_keyword(s, kw)
                if len(v) < 2:
                    v = _segment_containing(s, kw)
                if 2 <= len(v) <= 40:
                    score = 0
                    score += 5 if colon else 0
                    score += 2 if 2 <= len(v) <= 20 else 0
                    score -= 3 if len(v) > 25 else 0
                    cands.append((score, v, s))
                break
    if not cands:
        return None, '', 'low'

    # 实体后缀校验：配置了 entity_suffix 却抽不到合法实体时宁可留空，不编造
    suffixes = spec.get('entity_suffix') or []
    if suffixes:
        matched = [c for c in cands if any(c[1].endswith(x) for x in suffixes)]
        if not matched:
            return None, '', 'low'
        cands = matched

    best = max(cands, key=lambda c: (c[0], len(c[1])))
    conf = 'high' if best[0] >= 5 else 'medium'
    return best[1], clean_quote(best[2]), conf


def extract_sentence(sentences, spec):
    """摘取包含关键词的原句。按关键词命中数排序，取最相关的若干句。"""
    kws = spec.get('keywords', [])
    guard = spec.get('negation_guard', True)
    limit = spec.get('max_sentences', 3)
    scored = []
    for s in sentences:
        h = keyword_hits(s, kws, guard)
        if h:
            scored.append((h, norm_space(s)))
    if not scored:
        return None, '', 'low'
    scored.sort(key=lambda x: (-x[0],))
    uniq, seen = [], set()
    for h, s in scored:
        k = s[:30]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
        if len(uniq) >= limit:
            break
    value = '；'.join(uniq)
    conf = 'high' if scored[0][0] >= 2 else 'medium'
    return value, clean_quote(uniq[0]), conf


NUM_TOKEN_RE = re.compile(r'(?<![\d.])([0-9]+(?:\.\d+)?|[一二三四五六七八九十百千万两]{1,6})')
# 这些字紧跟在数字后面时，说明该数字并非独立数量（如"一共""一点"）
CN_NOT_QUANTITY = set('共起定些直样般切旦块半')


def _scan_numbers(s):
    """扫描句子中的数量。单位只做向后窥视、不消耗字符，保证相邻数字都能被检出。"""
    out = []
    for m in NUM_TOKEN_RE.finditer(s):
        raw = m.group(1)
        rest = m.end()
        mult = 1
        nxt = s[rest:rest + 1]
        if nxt in ('万', '千'):
            mult = 10000 if nxt == '万' else 1000
            rest += 1
        elif nxt in CN_NOT_QUANTITY:
            continue  # "一共是四栋" 中的"一"不是数量
        unit = s[rest:rest + 4]
        num = cn2num(raw)
        if num is None or not (0 < num * mult < 1e9):
            continue
        out.append((num * mult, unit, s[max(0, m.start() - 6): rest + 6], m.start()))
    return out


def extract_number(sentences, spec, text):
    """抽取数量。

    优先级：含总量线索（"一共/总共"）的句子 > 普通含关键词的句子。
    同一口径下出现多个不同数值时（自我更正），取最后一次表述并标记为待确认。
    """
    kws = spec.get('keywords', [])
    scoped = [s for s in sentences if any(k in s for k in kws)] or sentences
    cands = []
    for s in scoped:
        is_total = any(c in s for c in TOTAL_CUES)
        for num, unit, near, pos in _scan_numbers(s):
            if kws and not any(k in unit or k in near for k in kws):
                continue
            cands.append((int(num) if float(num).is_integer() else num, s, is_total))
    if not cands:
        return None, '', 'low'

    totals = [c for c in cands if c[2]]
    pool = totals or cands
    distinct = {c[0] for c in pool}
    if len(distinct) > 1:
        # 同一口径下出现多个数值 → 判定为原文冲突/自我更正，取最后一次表述
        last = pool[-1]
        snippets, seen = [], set()
        for c in pool[-3:]:
            q = clean_quote(c[1], 90)
            if q not in seen:
                seen.add(q)
                snippets.append(q)
        return last[0], '；'.join(snippets), 'low'
    return pool[0][0], clean_quote(pool[0][1]), 'high'


DATE_PATTERNS = [
    (re.compile(r'(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?'), True),
    (re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]'), False),
    (re.compile(r'(20\d{2})\s*年\s*(\d{1,2})\s*月(?!\s*\d)'), False),
]


def extract_date(sentences, spec, text):
    year = datetime.now().year
    for rx, full in DATE_PATTERNS:
        for s in sentences:
            m = rx.search(s)
            if not m:
                continue
            g = m.groups()
            if full:
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif len(g) == 2 and '月' in m.group(0) and '日' in m.group(0):
                y, mo, d = year, int(g[0]), int(g[1])
            else:
                y, mo, d = int(g[0]), int(g[1]), 1
            try:
                val = datetime(y, mo, d).strftime('%Y-%m-%d')
            except ValueError:
                continue
            return val, clean_quote(s), 'high' if full else 'medium'
    return None, '', 'low'


PHONE_PATTERN = re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)|(?<!\d)(0\d{2,3}-?\d{7,8})(?!\d)')


def extract_phone(text):
    m = PHONE_PATTERN.search(text or '')
    if not m:
        return None, '', 'low'
    val = m.group(1) or m.group(2)
    return val, clean_quote(text[max(0, m.start() - 20):m.end() + 10]), 'high'


MONEY_PATTERN = re.compile(
    r'([0-9]+(?:\.\d+)?|[一二三四五六七八九十百千万两]{1,6})\s*(万元|万|元|块)')


def extract_money(sentences, spec, text):
    kws = spec.get('keywords') or ['预算', '费用', '投入', '花', '钱', '价格']
    for s in sentences:
        if not any(k in s for k in kws):
            continue
        match = MONEY_PATTERN.search(s)
        if match:
            if spec.get('numeric_value'):
                value = cn2num(match.group(1))
                if match.group(2) in ('万元', '万'):
                    value *= 10000
                return value, clean_quote(s), 'medium'
            return norm_space(s), clean_quote(s), 'medium'
        if spec.get('numeric_value'):
            value, quote, confidence = extract_number([s], spec, s)
            if value is not None:
                return value, quote, confidence
    return None, '', 'low'


def _polarity(sentence, syn):
    """统计某个同义词在本句中的肯定/否定极性。"""
    score, start = 0, 0
    while True:
        i = sentence.find(syn, start)
        if i < 0:
            break
        pre = sentence[max(0, i - 6):i]
        post = sentence[i + len(syn): i + len(syn) + 6]
        is_neg = any(c in pre for c in NEG_PRE) or any(c in post for c in NEG_POST)
        score += -1 if is_neg else 1
        start = i + len(syn)
    return score


def _score_options(sentences, field, guard=True):
    scored = []
    for opt in field.get('options', []):
        total = 0
        for syn in [opt['value']] + list(opt.get('synonyms', [])):
            if not syn:
                continue
            weight = 2 if syn == opt['value'] else 1
            for s in sentences:
                if syn not in s:
                    continue
                p = _polarity(s, syn) if guard else s.count(syn)
                total += p * weight
        if total > 0:
            scored.append((total, opt['value']))
    scored.sort(key=lambda x: -x[0])
    return scored


def extract_enum(sentences, spec, field, text):
    if spec.get('context_keywords'):
        sentences = [s for s in sentences if any(k in s for k in spec['context_keywords'])]
    if spec.get('prefer_longest_match'):
        winners = set()
        for sentence in sentences:
            if any(word in sentence for word in ('是否', '不确定', '不清楚', '待核实')):
                return None, '', 'low'
            matches = [(m.start(), m.end(), option['value'], term)
                       for option in field.get('options', [])
                       for term in [option['value']] + option.get('synonyms', []) if term
                       for m in re.finditer(re.escape(term), sentence)]
            for start, end, value, term in matches:
                if any(left <= start and right >= end and right - left > end - start
                       for left, right, _, _ in matches):
                    continue
                if negated(sentence, term):
                    return None, '', 'low'
                winners.add(value)
        if len(winners) == 1:
            value = next(iter(winners))
            return value, _quote_for(sentences, field, value), 'high'
        # Conflicts are not guessed; the caller retains the source as custom text.
        return None, '', 'low'
    guard = spec.get('negation_guard', True)
    scored = _score_options(sentences, field, guard)
    if not scored:
        dft = spec.get('default')
        return (dft, '', 'low') if dft else (None, '', 'low')
    top = scored[0][0]
    ties = [v for s_, v in scored if s_ == top]
    quote = _quote_for(sentences, field, ties[0])
    if len(ties) > 1:
        return ties[0], quote, 'low'
    return ties[0], quote, 'high'


def extract_list(sentences, spec, field, text):
    if spec.get('context_keywords'):
        sentences = [s for s in sentences if any(k in s for k in spec['context_keywords'])]
    guard = spec.get('negation_guard', True)
    scored = _score_options(sentences, field, guard)
    if not scored:
        return None, '', 'low'
    values = [v for s_, v in scored if s_ > 0]
    return values, clean_quote('；'.join(_quote_for(sentences, field, v) for v in values)), 'high'


def _quote_for(sentences, field, value):
    opt = next((o for o in field.get('options', []) if o['value'] == value), None)
    words = [value] + (list(opt.get('synonyms', [])) if opt else [])
    for s in sentences:
        for w in words:
            if w and w in s and not negated(s, w):
                return clean_quote(s)
    for s in sentences:
        if any(w and w in s for w in words):
            return clean_quote(s)
    return ''


DEVICE_WORDS = ['摄像头', '摄像机', '探头', '枪机', '球机', '门禁', '闸机', '烟感',
                '报警器', '传感器', '录像机', 'NVR', 'nvr', '硬盘录像机']
BRAND_WORDS = ['海康威视', '海康', '大华', '宇视', '天地伟业', '华为', 'TP-LINK', 'tp-link']
QTY_RE = re.compile(r'([0-9]+|[一二三四五六七八九十百千万两]{1,4})\s*(台|个|只|路|支|套|把|块)')


def extract_table(sentences, spec, text):
    rows, seen = [], set()
    for s in sentences:
        for w in DEVICE_WORDS:
            if w not in s:
                continue
            qty = ''
            m = QTY_RE.search(s)
            if m:
                q = cn2num(m.group(1))
                if q:
                    qty = str(int(q))
            brand = next((b for b in BRAND_WORDS if b in s), '')
            status = ''
            if any(k in s for k in ['坏', '故障', '老化', '模糊', '不能用了', '不清']):
                status = '待更换'
            elif any(k in s for k in ['能用', '新', '刚装', '利旧', '正常']):
                status = '可复用'
            key = (w, brand, qty)
            if key in seen:
                continue
            seen.add(key)
            rows.append({'type': w, 'brand': brand, 'qty': qty, 'status': status})
    if not rows:
        return None, '', 'low'
    return rows[:20], clean_quote(rows[0]['type'] + ' 等设备描述'), 'medium'


# ------------------------------------------------------------------ 规则引擎

def _run_rule_engine(text, product_type, protected_keys):
    cfg = field_config(product_type)
    sentences = split_sentences(text)
    result, hit_fields = {}, 0

    for f in cfg['fields']:
        key = f['key']
        spec = f.get('extract') or {}
        mode = spec.get('mode', 'sentence')

        if mode == 'phone':
            value, quote, conf = extract_phone(text)
        elif mode == 'date':
            value, quote, conf = extract_date(sentences, spec, text)
        elif mode == 'number':
            value, quote, conf = extract_number(sentences, spec, text)
        elif mode == 'money':
            value, quote, conf = extract_money(sentences, spec, text)
        elif mode == 'enum':
            value, quote, conf = extract_enum(sentences, spec, f, text)
        elif mode == 'list':
            value, quote, conf = extract_list(sentences, spec, f, text)
        elif mode == 'table':
            value, quote, conf = extract_table(sentences, spec, text)
        elif mode == 'entity':
            value, quote, conf = extract_entity(sentences, spec)
        else:
            value, quote, conf = extract_sentence(sentences, spec)

        if f.get('allow_custom') and spec.get('fallback_text'):
            relevant = [s for s in sentences if any(k in s for k in spec.get('context_keywords', []))]
            original = '；'.join(relevant)
            if original and value in (None, '', [], {}):
                value = [original] if f['type'] == 'multiselect' else original
                quote, conf = clean_quote(original), 'low'
            elif original and f['type'] == 'multiselect' and spec.get('preserve_details'):
                if original not in value:
                    value = value + [original]
                quote = clean_quote(original)

        empty = value in (None, '', [], {})
        if empty:
            status = 'required_missing' if f.get('required') else 'optional_missing'
            conf = None
        else:
            hit_fields += 1
            status = 'pending_confirm' if conf == 'low' else 'extracted'

        result[key] = {'value': value, 'quote': quote, 'confidence': conf,
                       'status': status, 'protected': key in protected_keys}
    return result, hit_fields


# ------------------------------------------------------------------ LLM provider

LLM_SYSTEM = """你是资深服务方案顾问，负责按当前产品的字段定义把现场踏勘口语文字稿整理为结构化字段。
严格按 JSON 输出，不要编造。无法确定就留空字符串。"""


def llm_status():
    """返回可展示的连通前配置状态，不返回任何密钥内容。"""
    cfg = app_config()['extraction']['providers']['llm']
    return {
        'label': cfg.get('label', '大模型接口'),
        'model': read_env(cfg.get('model_env') or '', cfg.get('model') or ''),
        'configured': bool(
            cfg.get('enabled') and
            read_env(cfg.get('api_key_env') or 'LS_LLM_API_KEY') and
            read_env(cfg.get('base_url_env') or '', cfg.get('base_url') or '')),
    }


def _normalize_llm_value(field, value):
    """把模型输出收紧到字段契约；不合规值按缺失处理，禁止宽松落库。"""
    if value in (None, '', [], {}):
        return None
    ftype = field.get('type')
    if ftype == 'number':
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and re.fullmatch(r'-?\d+(?:\.\d+)?', value.strip()):
            number = float(value) if '.' in value else int(value)
            return number
        return None
    if ftype == 'select':
        allowed = {o.get('value') for o in field.get('options', [])}
        return value.strip() if isinstance(value, str) and (value in allowed or field.get('allow_custom')) else None
    if ftype == 'multiselect':
        allowed = {o.get('value') for o in field.get('options', [])}
        return list(dict.fromkeys(x.strip() for x in value if isinstance(x, str) and x.strip()
                                 and (x in allowed or field.get('allow_custom')))) if isinstance(value, list) else None
    if ftype in ('text', 'textarea', 'date'):
        return str(value).strip() if not isinstance(value, (list, dict)) else None
    return value


def _run_llm_engine(text, product_type, protected_keys):
    cfg = app_config()['extraction']['providers']['llm']
    base_url = (read_env(cfg.get('base_url_env') or '', cfg.get('base_url') or '')).rstrip('/')
    model = read_env(cfg.get('model_env') or '', cfg.get('model') or '')
    api_key = read_env(cfg.get('api_key_env') or 'LS_LLM_API_KEY')
    if not (base_url and model and api_key):
        raise ExtractionError('LLM provider 未配置完整（base_url / model / API Key）')

    fields = field_config(product_type)['fields']
    schema = []
    for f in fields:
        item = {'key': f['key'], 'label': f['label'], 'type': f['type'],
                'required': bool(f.get('required')), 'hint': f.get('ai_hint', '')}
        if f.get('options'):
            item['options'] = [o['value'] for o in f['options']]
            item['allow_custom'] = bool(f.get('allow_custom'))
        schema.append(item)

    prompt = (
        '请从下面的踏勘文字稿中提取字段。\n\n'
        '字段定义：\n' + json.dumps(schema, ensure_ascii=False) + '\n\n'
        '输出严格的 JSON 对象（不要 markdown 代码块），格式：\n'
        '{"fields": {"<key>": {"value": <值>, "quote": "<原文依据片段>", "confidence": "high|medium|low"}}}\n'
        '规则：\n'
        '1. 值必须与字段 type 匹配（number 用数字，multiselect 用数组）。\n'
        '2. 找不到就给空值，禁止臆测。\n'
        '3. 存在歧义或冲突时 confidence 给 low。\n'
        '4. quote 必须是原文中真实存在的片段。\n'
        '5. 选择题优先使用 options；只有 allow_custom=true 时允许补充原文连续片段，不能编写新的选项。\n'
        '6. 多选的具体楼层、点位、数量和优先级等细节请以原文片段追加到数组中。\n\n'
        '文字稿：\n' + text)

    body = {
        'model': model,
        'messages': [{'role': 'system', 'content': LLM_SYSTEM},
                     {'role': 'user', 'content': prompt}],
        'temperature': 0.1,
    }
    if cfg.get('json_mode'):
        body['response_format'] = {'type': 'json_object'}
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        base_url + '/chat/completions', data=payload,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get('timeout', 120)) as resp:
            raw = json.loads(resp.read().decode('utf-8'))
        content = raw['choices'][0]['message']['content']
        content = re.sub(r'^```(?:json)?|```$', '', content.strip(), flags=re.M).strip()
        data = json.loads(content)
    except urllib.error.HTTPError as exc:
        # 响应正文可能含厂商诊断信息或请求内容，不写入运行记录。
        raise ExtractionError('大模型接口请求失败（HTTP %s），请检查额度、模型和密钥。' % exc.code) from exc
    except urllib.error.URLError as exc:
        raise ExtractionError('无法连接大模型接口，请检查网络或接口地址。') from exc
    except TimeoutError as exc:
        raise ExtractionError('大模型接口响应超时，请稍后重试。') from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExtractionError('大模型返回格式不符合结构化 JSON 契约，请重试。') from exc

    out, hit_fields = {}, 0
    for f in fields:
        got = (data.get('fields') or {}).get(f['key']) or {}
        value = _normalize_llm_value(f, got.get('value'))
        if f.get('allow_custom') and f['type'] in ('select', 'multiselect'):
            allowed = {o['value'] for o in f.get('options', [])}
            if isinstance(value, list):
                value = [v for v in value if v in allowed or v in text]
            elif value and value not in allowed and value not in text:
                value = None
        empty = value in (None, '', [], {})
        if empty:
            status = 'required_missing' if f.get('required') else 'optional_missing'
            conf = None
        else:
            hit_fields += 1
            conf = got.get('confidence') if got.get('confidence') in ('high', 'medium', 'low') else 'medium'
            status = 'pending_confirm' if conf == 'low' else 'extracted'
        quote = clean_quote(got.get('quote', ''))
        if quote and quote not in text:
            quote = ''
            if not empty:
                conf = 'low'
                status = 'pending_confirm'
        out[f['key']] = {'value': value, 'quote': quote,
                         'confidence': conf, 'status': status,
                         'protected': f['key'] in protected_keys}
    return out, hit_fields


# ------------------------------------------------------------------ 对外入口

def relevance_check(text, product_type):
    """判断文字稿与当前产品是否相关，避免无意义解析。"""
    cfg = app_config()['extraction']
    hits = 0
    for f in field_config(product_type)['fields']:
        spec = f.get('extract') or {}
        kws = list(spec.get('keywords') or [])
        if spec.get('mode') in ('enum', 'list'):
            kws += [s for o in f.get('options', []) for s in o.get('synonyms', [])]
        if any(k in text for k in kws):
            hits += 1
    return hits >= cfg.get('relevance_min_keyword_hits', 2), hits


def run_extraction(text, product_type, protected_keys=None, provider=None):
    """执行一次提取，返回 (result_dict, stats)。受保护字段不覆盖。"""
    protected_keys = set(protected_keys or [])
    cfg = app_config()['extraction']
    provider = provider or cfg.get('provider', 'rule')
    text = (text or '').strip()

    if len(text) < cfg.get('min_transcript_chars', 30):
        raise ExtractionError('文字稿过短（少于 %d 字），信息不足以提取结构化内容。'
                              % cfg.get('min_transcript_chars', 30))
    if len(text) > cfg.get('max_transcript_chars', 50000):
        raise ExtractionError('文字稿超过 %d 字上限，请拆分后录入。'
                              % cfg.get('max_transcript_chars', 50000))

    ok, hits = relevance_check(text, product_type)
    if not ok:
        raise ExtractionError(
            '文字稿内容与「%s」业务关联度过低（仅命中 %d 个字段线索）。'
            '请确认粘贴的是该产品调研相关的访谈内容。' % (product_config(product_type)['name'], hits))

    if provider == 'llm':
        result, hit_fields = _run_llm_engine(text, product_type, protected_keys)
    else:
        result, hit_fields = _run_rule_engine(text, product_type, protected_keys)

    stats = {'provider': provider, 'hit_fields': hit_fields, 'relevance_hits': hits,
             'protected_skipped': len(protected_keys), 'total_fields': len(result)}
    return result, stats
