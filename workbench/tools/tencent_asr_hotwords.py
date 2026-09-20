#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验并同步安全管家腾讯云 ASR 热词表。"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .tencent_asr_smoke import (
        SmokeTestError,
        build_clients,
        import_sdks,
        load_settings,
    )
except ImportError:  # 直接执行本文件时，tools 目录位于 sys.path。
    from tencent_asr_smoke import (
        SmokeTestError,
        build_clients,
        import_sdks,
        load_settings,
    )


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "products"
    / "safety_butler"
    / "asr_terms.json"
)


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeTestError(f"找不到配置文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"配置文件不是有效 JSON：{exc}") from exc
    return data


def is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def render_hotwords(config: dict[str, Any]) -> str:
    hotwords = config.get("hotwords")
    if not isinstance(hotwords, list) or not hotwords:
        raise SmokeTestError("hotwords 必须是非空数组。")
    if len(hotwords) > 1000:
        raise SmokeTestError("腾讯云单个热词表最多 1000 个词。")

    engine = config.get("provider", {}).get("engine_model_type", "")
    seen: set[str] = set()
    lines: list[str] = []
    for index, item in enumerate(hotwords, start=1):
        term = str(item.get("term", "")).strip()
        weight = item.get("weight")
        if not term:
            raise SmokeTestError(f"第 {index} 个热词为空。")
        if term in seen:
            raise SmokeTestError(f"热词重复：{term}")
        if "|" in term or "\n" in term or "\r" in term:
            raise SmokeTestError(f"热词包含非法分隔符或换行：{term}")
        chinese_count = sum(1 for char in term if is_cjk(char))
        if chinese_count > 10 or len(term) > 30:
            raise SmokeTestError(f"热词超出腾讯云长度限制：{term}")
        if not isinstance(weight, int) or weight not in {*range(1, 12), 100}:
            raise SmokeTestError(f"热词权重非法：{term}|{weight}")
        if weight == 100 and engine not in {"8k_zh", "16k_zh"}:
            raise SmokeTestError(
                f"{engine} 不支持权重 100 的强制同音替换：{term}"
            )
        seen.add(term)
        lines.append(f"{term}|{weight}")
    return "\n".join(lines) + "\n"


def validate_generated_file(config_path: Path, config: dict[str, Any], text: str) -> None:
    relative = config.get("provider", {}).get("generated_file")
    if not relative:
        raise SmokeTestError("provider.generated_file 未配置。")
    generated_path = config_path.parent / relative
    try:
        current = generated_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SmokeTestError(f"找不到腾讯云导入文件：{generated_path}") from exc
    if current.replace("\r\n", "\n") != text:
        raise SmokeTestError(
            f"{generated_path.name} 与 asr_terms.json 不一致，请重新生成。"
        )


def get_client() -> tuple[Any, Any]:
    settings = load_settings()
    sdk = import_sdks()
    _, client = build_clients(settings, sdk)
    return client, sdk["models"]


def list_vocabs(client: Any, models: Any) -> list[Any]:
    request = models.GetAsrVocabListRequest()
    request.from_json_string(json.dumps({"Offset": 0, "Limit": 100}))
    response = client.GetAsrVocabList(request)
    return list(response.VocabList or [])


def encoded_hotwords(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def sync(config: dict[str, Any], text: str) -> tuple[str, str]:
    provider = config["provider"]
    name = provider["vocab_name"]
    description = (
        f"安全管家踏勘 ASR 热词；配置版本 {config['version']}；"
        f"{len(config['hotwords'])} 词"
    )
    client, models = get_client()
    matches = [item for item in list_vocabs(client, models) if item.Name == name]
    if len(matches) > 1:
        raise SmokeTestError(f"腾讯云存在 {len(matches)} 个同名热词表，请先人工处理：{name}")

    payload = {
        "Name": name,
        "Description": description,
        "WordWeightStr": encoded_hotwords(text),
    }
    if matches:
        vocab_id = matches[0].VocabId
        request = models.UpdateAsrVocabRequest()
        request.from_json_string(json.dumps({**payload, "VocabId": vocab_id}))
        response = client.UpdateAsrVocab(request)
        return "updated", response.VocabId

    request = models.CreateAsrVocabRequest()
    request.from_json_string(json.dumps(payload))
    response = client.CreateAsrVocab(request)
    return "created", response.VocabId


def get_status(vocab_id: str) -> dict[str, Any]:
    client, models = get_client()
    request = models.GetAsrVocabRequest()
    request.from_json_string(json.dumps({"VocabId": vocab_id}))
    response = client.GetAsrVocab(request)
    return {
        "name": response.Name,
        "vocab_id": response.VocabId,
        "state": response.State,
        "word_count": len(response.WordWeights or []),
        "updated_at": response.UpdateTime,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="只校验本地配置和导入文件")
    subparsers.add_parser("list", help="列出腾讯云热词表")
    subparsers.add_parser("sync", help="按名称创建或更新腾讯云热词表")
    status = subparsers.add_parser("status", help="查询腾讯云热词表状态")
    status.add_argument("vocab_id")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config_path = args.config.expanduser().resolve()
        config = load_config(config_path)
        text = render_hotwords(config)
        validate_generated_file(config_path, config, text)
        print(
            f"本地校验通过：{len(config['hotwords'])} 个热词，"
            f"{len(config.get('corrections', []))} 条纠错规则。"
        )

        if args.command == "check":
            return 0
        if args.command == "list":
            client, models = get_client()
            vocabs = list_vocabs(client, models)
            if not vocabs:
                print("腾讯云当前没有热词表。")
            for item in vocabs:
                print(f"{item.Name}\t{item.VocabId}\tstate={item.State}")
            return 0
        if args.command == "sync":
            action, vocab_id = sync(config, text)
            print(f"腾讯云同步完成：{action}，VocabId={vocab_id}")
            print("后续转写参数：--hotword-id", vocab_id)
            return 0
        if args.command == "status":
            print(json.dumps(get_status(args.vocab_id), ensure_ascii=False, indent=2))
            return 0
    except SmokeTestError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"腾讯云调用失败：{exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
