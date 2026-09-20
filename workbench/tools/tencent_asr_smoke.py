#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""腾讯云超长录音转写独立联调工具。

本工具不依赖 Flask 页面和工作台数据库。它完成：
本地音频 -> 私有 COS -> 预签名 URL -> CreateRecTask -> 轮询 -> 本地结果文件。

用法：
    python tools/tencent_asr_smoke.py check
    python tools/tencent_asr_smoke.py run "D:\\audio\\survey.m4a"
    python tools/tencent_asr_smoke.py resume data/asr_smoke/20260919_120000_ab12cd34
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".flv", ".mp4", ".wma", ".3gp",
    ".amr", ".aac", ".ogg", ".flac",
}
MAX_FILE_BYTES = 1024 * 1024 * 1024
DEFAULT_ENGINE = "16k_zh_en_2.0"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "asr_smoke"
STATE_FILE = "state.json"


class SmokeTestError(RuntimeError):
    """可向操作者直接显示的联调错误。"""


@dataclass(frozen=True)
class Settings:
    secret_id: str
    secret_key: str
    bucket: str
    region: str
    cos_prefix: str = "asr-mvp"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    names = (
        "TENCENTCLOUD_SECRET_ID",
        "TENCENTCLOUD_SECRET_KEY",
        "TENCENT_COS_BUCKET",
        "TENCENT_COS_REGION",
    )
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        raise SmokeTestError("缺少环境变量：" + ", ".join(missing))

    bucket = env["TENCENT_COS_BUCKET"].strip()
    region = env["TENCENT_COS_REGION"].strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-\d+", bucket):
        raise SmokeTestError(
            "TENCENT_COS_BUCKET 应为完整桶名，例如 lingshi-asr-1250000000。"
        )
    if not re.fullmatch(r"ap-[a-z0-9-]+", region):
        raise SmokeTestError(
            "TENCENT_COS_REGION 应为地域代码，例如 ap-shanghai 或 ap-guangzhou。"
        )
    prefix = env.get("TENCENT_COS_PREFIX", "asr-mvp").strip().strip("/")
    if not prefix:
        raise SmokeTestError("TENCENT_COS_PREFIX 不能是空字符串。")
    return Settings(
        secret_id=env["TENCENTCLOUD_SECRET_ID"].strip(),
        secret_key=env["TENCENTCLOUD_SECRET_KEY"].strip(),
        bucket=bucket,
        region=region,
        cos_prefix=prefix,
    )


def import_sdks() -> dict[str, Any]:
    try:
        from qcloud_cos import CosConfig, CosS3Client
        from tencentcloud.asr.v20190614 import asr_client, models
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
    except ImportError as exc:
        raise SmokeTestError(
            "腾讯云 SDK 尚未安装。请先执行：\n"
            "python -m pip install -r requirements-asr.txt"
        ) from exc
    return {
        "CosConfig": CosConfig,
        "CosS3Client": CosS3Client,
        "asr_client": asr_client,
        "models": models,
        "credential": credential,
        "ClientProfile": ClientProfile,
        "HttpProfile": HttpProfile,
    }


def validate_audio(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise SmokeTestError(f"找不到音频文件：{path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise SmokeTestError(f"不支持 {path.suffix or '无扩展名'}；允许格式：{allowed}")
    size = path.stat().st_size
    if size <= 0:
        raise SmokeTestError("音频文件为空。")
    if size > MAX_FILE_BYTES:
        raise SmokeTestError("音频文件超过 CreateRecTask URL 方式的 1GB 上限。")
    return path


def new_run_dir(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{stamp}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def load_state(run_dir: Path) -> dict[str, Any]:
    path = run_dir / STATE_FILE
    if not path.is_file():
        raise SmokeTestError(f"找不到任务状态文件：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeTestError(f"任务状态文件不可读：{path}") from exc


def update_state(run_dir: Path, state: dict[str, Any], **changes: Any) -> None:
    state.update(changes)
    state["updated_at"] = utc_now()
    save_json(run_dir / STATE_FILE, state)


def build_clients(settings: Settings, sdk: dict[str, Any]) -> tuple[Any, Any]:
    cos_config = sdk["CosConfig"](
        Region=settings.region,
        SecretId=settings.secret_id,
        SecretKey=settings.secret_key,
        Scheme="https",
    )
    cos_client = sdk["CosS3Client"](cos_config)

    cred = sdk["credential"].Credential(settings.secret_id, settings.secret_key)
    http_profile = sdk["HttpProfile"]()
    http_profile.endpoint = "asr.tencentcloudapi.com"
    http_profile.reqTimeout = 60
    client_profile = sdk["ClientProfile"]()
    client_profile.httpProfile = http_profile
    asr_client = sdk["asr_client"].AsrClient(cred, "", client_profile)
    return cos_client, asr_client


def make_object_key(settings: Settings, audio: Path) -> str:
    date_part = datetime.now().strftime("%Y/%m/%d")
    token = uuid.uuid4().hex
    return f"{settings.cos_prefix}/{date_part}/{token}{audio.suffix.lower()}"


def upload_audio(cos_client: Any, settings: Settings, audio: Path, key: str) -> None:
    last_printed = -1

    def progress(completed: int, total: int) -> None:
        nonlocal last_printed
        pct = int(completed * 100 / total) if total else 0
        bucket = pct // 10
        if bucket != last_printed:
            last_printed = bucket
            print(f"  上传进度：{min(pct, 100)}%", flush=True)

    cos_client.upload_file(
        Bucket=settings.bucket,
        Key=key,
        LocalFilePath=str(audio),
        PartSize=10,
        MAXThread=5,
        EnableMD5=False,
        progress_callback=progress,
    )


def presigned_download_url(
    cos_client: Any, settings: Settings, key: str, expires_seconds: int = 6 * 3600
) -> str:
    # 预签名 URL 包含临时鉴权信息，只保存在内存，不写入 state.json 或控制台。
    return cos_client.get_presigned_url(
        Method="GET",
        Bucket=settings.bucket,
        Key=key,
        Expired=expires_seconds,
    )


def submit_task(
    client: Any,
    models: Any,
    url: str,
    engine: str,
    hotword_id: str | None,
    speaker_diarization: bool,
) -> tuple[int, dict[str, Any]]:
    params: dict[str, Any] = {
        "EngineModelType": engine,
        "ChannelNum": 1,
        "ResTextFormat": 1,
        "SourceType": 0,
        "Url": url,
        "SpeakerDiarization": 1 if speaker_diarization else 0,
        "SpeakerNumber": 0,
        "FilterDirty": 0,
        "FilterModal": 0,
        "FilterPunc": 0,
        "ConvertNumMode": 1,
    }
    if hotword_id:
        params["HotwordId"] = hotword_id
    request = models.CreateRecTaskRequest()
    request.from_json_string(json.dumps(params, ensure_ascii=False))
    response = client.CreateRecTask(request)
    payload = json.loads(response.to_json_string())
    task_id = int(payload["Data"]["TaskId"])
    return task_id, payload


def describe_task(client: Any, models: Any, task_id: int) -> dict[str, Any]:
    request = models.DescribeTaskStatusRequest()
    request.from_json_string(json.dumps({"TaskId": task_id}))
    response = client.DescribeTaskStatus(request)
    return json.loads(response.to_json_string())


def poll_task(
    client: Any,
    models: Any,
    task_id: int,
    run_dir: Path,
    state: dict[str, Any],
    poll_seconds: int,
    timeout_minutes: int,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_minutes * 60
    last_status = None
    while True:
        payload = describe_task(client, models, task_id)
        data = payload.get("Data") or {}
        status = int(data.get("Status", -1))
        status_str = str(data.get("StatusStr") or status)
        update_state(
            run_dir,
            state,
            status=status_str,
            provider_status=status,
            last_polled_at=utc_now(),
        )
        if status_str != last_status:
            print(f"  腾讯任务状态：{status_str}", flush=True)
            last_status = status_str
        if status == 2:
            return payload
        if status == 3:
            message = data.get("ErrorMsg") or "腾讯云返回任务失败"
            raise SmokeTestError(str(message))
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待超过 {timeout_minutes} 分钟。可稍后用 resume 命令继续查询。"
            )
        sleep_fn(poll_seconds)


def format_timestamp(ms: int | None) -> str:
    value = max(0, int(ms or 0))
    hours, rem = divmod(value, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def write_outputs(run_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    data = payload.get("Data") or {}
    details = data.get("ResultDetail") or []
    provider_text = str(data.get("Result") or "").strip()
    # Result 可能自带形如 [0:0.020,0:2.380] 的厂商标记；送入现有提取
    # 流程的 transcript.txt 应只包含干净句子，因此优先使用 ResultDetail。
    result_text = "\n".join(
        str(item.get("FinalSentence") or "").strip()
        for item in details
        if item.get("FinalSentence")
    )
    if not result_text:
        result_text = provider_text

    raw_path = run_dir / "result.raw.json"
    provider_text_path = run_dir / "provider_result.txt"
    text_path = run_dir / "transcript.txt"
    md_path = run_dir / "transcript.with_timestamps.md"
    segments_path = run_dir / "segments.json"
    save_json(raw_path, payload)
    save_json(segments_path, details)
    provider_text_path.write_text(
        provider_text + ("\n" if provider_text else ""), encoding="utf-8"
    )
    text_path.write_text(result_text + ("\n" if result_text else ""), encoding="utf-8")

    duration = data.get("AudioDuration")
    lines = [
        "# 腾讯云录音转写结果",
        "",
        f"- TaskId：{data.get('TaskId', '')}",
        f"- 音频时长：{duration if duration is not None else '未知'} 秒",
        f"- 分段数量：{len(details)}",
        "",
        "## 分段文字",
        "",
    ]
    for item in details:
        text = str(item.get("FinalSentence") or "").strip()
        if not text:
            continue
        start = format_timestamp(item.get("StartMs"))
        end = format_timestamp(item.get("EndMs"))
        speaker = item.get("SpeakerId")
        speaker_label = f"说话人 {speaker}" if speaker is not None else "说话人未知"
        lines.append(f"- [{start} - {end}] **{speaker_label}**：{text}")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "raw": raw_path,
        "provider_text": provider_text_path,
        "text": text_path,
        "markdown": md_path,
        "segments": segments_path,
    }


def delete_cos_object(cos_client: Any, settings: Settings, key: str) -> None:
    cos_client.delete_object(Bucket=settings.bucket, Key=key)


def cleanup_cos_object(
    cos_client: Any,
    settings: Settings,
    key: str,
    run_dir: Path,
    state: dict[str, Any],
) -> bool:
    """尽力清理临时对象；清理失败不覆盖已经取得的转写结果。"""
    try:
        delete_cos_object(cos_client, settings, key)
    except Exception as exc:
        update_state(run_dir, state, cos_cleanup_error=f"{type(exc).__name__}: {exc}")
        print("警告：COS 临时音频删除失败，请稍后在控制台手工删除。", file=sys.stderr)
        return False
    update_state(run_dir, state, cos_deleted_at=utc_now(), cos_cleanup_error=None)
    return True


def command_check(_args: argparse.Namespace) -> int:
    settings = load_settings()
    import_sdks()
    print("本机配置检查通过。")
    print(f"  COS 存储桶：{settings.bucket}")
    print(f"  COS 地域：{settings.region}")
    print(f"  COS 前缀：{settings.cos_prefix}/")
    print("  SecretId / SecretKey：已设置（值未显示）")
    print("  腾讯云 Python SDK：已安装")
    return 0


def command_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    sdk = import_sdks()
    audio = validate_audio(args.audio)
    output_root = Path(args.output_root).expanduser().resolve()
    run_dir = new_run_dir(output_root)
    object_key = make_object_key(settings, audio)
    state: dict[str, Any] = {
        "run_id": run_dir.name,
        "status": "created",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "audio_path": str(audio),
        "audio_size": audio.stat().st_size,
        "bucket": settings.bucket,
        "region": settings.region,
        "object_key": object_key,
        "engine": args.engine,
        "hotword_id": args.hotword_id or None,
        "speaker_diarization": not args.no_speaker_diarization,
        "keep_cos": bool(args.keep_cos),
    }
    save_json(run_dir / STATE_FILE, state)
    print(f"联调任务目录：{run_dir}")

    cos_client, asr_client = build_clients(settings, sdk)
    uploaded = False
    try:
        print("1/4 正在上传音频到私有 COS……")
        upload_audio(cos_client, settings, audio, object_key)
        uploaded = True
        update_state(run_dir, state, status="uploaded", uploaded_at=utc_now())

        url = presigned_download_url(cos_client, settings, object_key)
        print("2/4 正在创建腾讯云录音识别任务……")
        task_id, create_payload = submit_task(
            asr_client,
            sdk["models"],
            url,
            args.engine,
            args.hotword_id,
            not args.no_speaker_diarization,
        )
        save_json(run_dir / "create_task.response.json", create_payload)
        update_state(
            run_dir,
            state,
            status="submitted",
            provider_task_id=task_id,
            submitted_at=utc_now(),
        )
    except Exception:
        update_state(run_dir, state, status="failed_before_submit")
        if uploaded and not args.keep_cos:
            cleanup_cos_object(cos_client, settings, object_key, run_dir, state)
        raise
    print(f"  TaskId：{task_id}")
    print("3/4 正在轮询识别结果……")
    try:
        result = poll_task(
            asr_client,
            sdk["models"],
            task_id,
            run_dir,
            state,
            args.poll_seconds,
            args.timeout_minutes,
        )
    except (KeyboardInterrupt, TimeoutError):
        update_state(run_dir, state, status="interrupted")
        print("\n查询已停止；COS 文件未删除，可使用 resume 命令继续。", file=sys.stderr)
        print(f'python tools/tencent_asr_smoke.py resume "{run_dir}"', file=sys.stderr)
        return 2
    except Exception:
        update_state(run_dir, state, status="failed")
        if not args.keep_cos:
            cleanup_cos_object(cos_client, settings, object_key, run_dir, state)
        raise

    print("4/4 正在保存本地结果……")
    outputs = write_outputs(run_dir, result)
    update_state(
        run_dir,
        state,
        status="success",
        completed_at=utc_now(),
        audio_duration=(result.get("Data") or {}).get("AudioDuration"),
        outputs={key: str(value) for key, value in outputs.items()},
    )
    if not args.keep_cos:
        if cleanup_cos_object(cos_client, settings, object_key, run_dir, state):
            print("  COS 临时音频已删除。")
    print("转写成功：")
    for label, path in outputs.items():
        print(f"  {label}: {path}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    settings = load_settings()
    sdk = import_sdks()
    run_dir = Path(args.run_dir).expanduser().resolve()
    state = load_state(run_dir)
    task_id = state.get("provider_task_id")
    if not task_id:
        raise SmokeTestError("该任务尚未成功提交，没有 provider_task_id。")
    if state.get("status") == "success":
        print("该任务已经成功，无需继续查询。")
        return 0
    if state.get("bucket") != settings.bucket or state.get("region") != settings.region:
        raise SmokeTestError("当前环境变量中的 COS 桶/地域与原任务不一致。")

    cos_client, asr_client = build_clients(settings, sdk)
    print(f"继续查询 TaskId：{task_id}")
    try:
        result = poll_task(
            asr_client,
            sdk["models"],
            int(task_id),
            run_dir,
            state,
            args.poll_seconds,
            args.timeout_minutes,
        )
    except (KeyboardInterrupt, TimeoutError):
        update_state(run_dir, state, status="interrupted")
        print("\n查询已停止；稍后可再次执行相同 resume 命令。", file=sys.stderr)
        return 2
    except Exception:
        update_state(run_dir, state, status="failed")
        if not (args.keep_cos or state.get("keep_cos")) and state.get("object_key"):
            cleanup_cos_object(
                cos_client, settings, state["object_key"], run_dir, state
            )
        raise

    outputs = write_outputs(run_dir, result)
    update_state(
        run_dir,
        state,
        status="success",
        completed_at=utc_now(),
        audio_duration=(result.get("Data") or {}).get("AudioDuration"),
        outputs={key: str(value) for key, value in outputs.items()},
    )
    if not (args.keep_cos or state.get("keep_cos")) and state.get("object_key"):
        if cleanup_cos_object(
            cos_client, settings, state["object_key"], run_dir, state
        ):
            print("COS 临时音频已删除。")
    print(f"转写成功：{outputs['markdown']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="腾讯云超长录音转写独立联调工具（不依赖工作台页面）。"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="检查环境变量和 SDK 是否就绪，不访问云端")
    check.set_defaults(func=command_check)

    run = sub.add_parser("run", help="上传音频、提交任务、等待并保存结果")
    run.add_argument("audio", help="本地音频文件路径")
    run.add_argument("--engine", default=DEFAULT_ENGINE, help="ASR 引擎模型")
    run.add_argument("--hotword-id", default=None, help="腾讯云热词表 ID")
    run.add_argument(
        "--no-speaker-diarization", action="store_true", help="关闭说话人分离"
    )
    run.add_argument("--keep-cos", action="store_true", help="成功后仍保留 COS 音频")
    run.add_argument("--poll-seconds", type=int, default=8, help="轮询间隔，默认 8 秒")
    run.add_argument(
        "--timeout-minutes", type=int, default=180, help="本次等待上限，默认 180 分钟"
    )
    run.add_argument(
        "--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="本地结果根目录"
    )
    run.set_defaults(func=command_run)

    resume = sub.add_parser("resume", help="继续查询已提交但中断的任务")
    resume.add_argument("run_dir", help="包含 state.json 的联调任务目录")
    resume.add_argument("--keep-cos", action="store_true", help="成功后仍保留 COS 音频")
    resume.add_argument("--poll-seconds", type=int, default=8, help="轮询间隔，默认 8 秒")
    resume.add_argument(
        "--timeout-minutes", type=int, default=180, help="本次等待上限，默认 180 分钟"
    )
    resume.set_defaults(func=command_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "poll_seconds", 1) < 1:
        parser.error("--poll-seconds 必须大于等于 1")
    if getattr(args, "timeout_minutes", 1) < 1:
        parser.error("--timeout-minutes 必须大于等于 1")
    try:
        return int(args.func(args))
    except SmokeTestError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"未预期错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
