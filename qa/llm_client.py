from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


MAX_PROMPT_CHARS = int(os.environ.get('QA_LLM_MAX_PROMPT_CHARS', '6000'))
DEFAULT_TIMEOUT = int(os.environ.get('QA_LLM_TIMEOUT_SECONDS', '60'))


class LlmJsonError(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get('QA_LLM_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}


def _load_fake_responses() -> dict[str, Any]:
    raw = os.environ.get('QA_LLM_FAKE_RESPONSES_JSON')
    path = os.environ.get('QA_LLM_FAKE_RESPONSES_FILE')
    if raw:
        return json.loads(raw)
    if path:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def compact_card_payload(context: dict, *, include_upstream: bool = True) -> dict:
    """Return a token-conscious card packet for LLM semantic agents.

    The caller must pass only this compact payload, not full run artifacts or full
    indexes. Deterministic preflight remains responsible for retrieval; the LLM
    sees just card text and relevant hit summaries.
    """
    pack = context.get('context_pack') or {}
    payload = {
        'card_id': context.get('code'),
        'item_id': context.get('item_id'),
        'source_text': context.get('source_text', ''),
        'current_ko': context.get('current_ko', ''),
        'polished_ko': context.get('polished_ko'),
        'term_hits': _limit(pack.get('terminology_hits') or context.get('terminology_result', {}).get('term_checks') or [], 8),
        'syntax_hits': _limit(pack.get('syntax_hits') or context.get('syntax_pattern_result', {}).get('checks') or [], 6),
        'ontology_hits': _limit(context.get('ontology_result', {}).get('checks') or [], 6),
        'rulebook_hits': _limit(pack.get('rulebook_hits') or context.get('rulebook_hits') or [], 6),
        'issues_so_far': _limit(context.get('facts', {}).get('issues') or [], 8),
    }
    if include_upstream:
        payload['source_analysis'] = context.get('source_analysis')
        payload['translation_slot_result'] = context.get('translation_slot_result')
        payload['rules_lawyer_result'] = context.get('rules_lawyer_result')
        payload['translation_comparison'] = context.get('translation_comparison')
        payload['im_not_ai_result'] = context.get('im_not_ai_result')
        payload['self_verification'] = context.get('self_verification')
    return payload


def _limit(value: Any, n: int) -> Any:
    if isinstance(value, list):
        return value[:n]
    if isinstance(value, dict):
        return dict(list(value.items())[:n])
    return value


def build_prompt(agent_name: str, task: str, payload: dict, expected_schema: dict) -> str:
    prompt = {
        'agent_name': agent_name,
        'task': task,
        'hard_rules': [
            'Return JSON only; no markdown.',
            'Use UNKNOWN and needs_human_review=true when evidence is insufficient.',
            'Do not invent glossary, lore, or rulebook facts not present in payload.',
            'Prefer concise evidence strings anchored to source_text/current_ko.',
            'Never auto-apply; suggestions are proposal-only.',
        ],
        'expected_schema': expected_schema,
        'payload': payload,
    }
    text = json.dumps(prompt, ensure_ascii=False, separators=(',', ':'))
    if len(text) > MAX_PROMPT_CHARS:
        payload = dict(payload)
        for key in ['issues_so_far', 'term_hits', 'syntax_hits', 'ontology_hits', 'rulebook_hits']:
            payload[key] = _limit(payload.get(key), 3)
        prompt['payload'] = payload
        text = json.dumps(prompt, ensure_ascii=False, separators=(',', ':'))
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS]
    return text


def call_json(agent_name: str, prompt: str, expected_keys: list[str] | None = None) -> dict:
    """Call an optional JSON LLM and return telemetry + parsed JSON.

    Supported modes:
    - fake responses for tests: QA_LLM_FAKE_RESPONSES_JSON/FILE
    - OpenAI-compatible chat completion: QA_LLM_BASE_URL, QA_LLM_API_KEY, QA_LLM_MODEL

    If disabled or unavailable, returns used=false and never raises into agents.
    """
    usage = {
        'enabled': enabled(),
        'used': False,
        'provider': os.environ.get('QA_LLM_PROVIDER') or 'openai-compatible',
        'model': os.environ.get('QA_LLM_MODEL'),
        'input_chars': len(prompt),
        'output_chars': 0,
        'error': None,
    }
    if not enabled():
        return {'usage': usage, 'data': None}
    try:
        fake = _load_fake_responses()
        if agent_name in fake:
            data = fake[agent_name]
            raw = json.dumps(data, ensure_ascii=False)
            usage.update({'used': True, 'provider': 'fake', 'model': 'fake-json', 'output_chars': len(raw)})
            _validate(data, expected_keys)
            return {'usage': usage, 'data': data}

        api_key = os.environ.get('QA_LLM_API_KEY') or os.environ.get('OPENAI_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
        base_url = os.environ.get('QA_LLM_BASE_URL') or os.environ.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1'
        model = os.environ.get('QA_LLM_MODEL')
        if not api_key or not model:
            usage['error'] = 'missing_api_key_or_model'
            return {'usage': usage, 'data': None}
        raw = _chat_completion(base_url.rstrip('/'), api_key, model, prompt)
        usage.update({'used': True, 'output_chars': len(raw)})
        data = _parse_json(raw)
        _validate(data, expected_keys)
        return {'usage': usage, 'data': data}
    except Exception as exc:  # agents must fall back deterministically
        usage['error'] = f'{type(exc).__name__}: {exc}'
        return {'usage': usage, 'data': None}


def record_usage(context: dict, agent_name: str, usage: dict) -> None:
    context.setdefault('llm_usage', {})[agent_name] = usage


def _chat_completion(base_url: str, api_key: str, model: str, prompt: str) -> str:
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are a boardgame translation QA agent. Return strict JSON only.'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': float(os.environ.get('QA_LLM_TEMPERATURE', '0')),
        'response_format': {'type': 'json_object'},
    }
    req = urllib.request.Request(
        f'{base_url}/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': os.environ.get('QA_LLM_HTTP_REFERER', 'https://github.com/itchy3/qa-12-agents'),
            'X-Title': os.environ.get('QA_LLM_TITLE', 'qa-12-agents'),
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    return payload['choices'][0]['message']['content']


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise LlmJsonError('top-level JSON is not an object')
    return data


def _validate(data: dict, expected_keys: list[str] | None) -> None:
    if not isinstance(data, dict):
        raise LlmJsonError('top-level JSON is not an object')
    if expected_keys:
        missing = [key for key in expected_keys if key not in data]
        if missing:
            raise LlmJsonError(f'missing expected key(s): {missing}')
