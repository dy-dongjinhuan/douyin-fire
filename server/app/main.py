from __future__ import annotations



import argparse

import asyncio

import json

import logging

import random

import re

import hashlib

from dataclasses import asdict

from datetime import datetime

from pathlib import Path



from app.browser import AuthenticationError, RiskControlError, SearchBoxNotReadyError, open_douyin, open_private_messages, save_trace, verify_login

from app.config import ConfigError, load_settings, load_task

from app.douyin import DouyinChat

from app.history import AlreadyRunningError, History, run_lock

from app.models import Message, Settings, TargetResult
from app.ai_text import generate_ai_text

from app.notifier import send_dingtalk_notification

from app.privacy import RedactingFormatter, build_target_aliases, redact_text, target_alias

from app.sender import send_message





LOGGER = logging.getLogger("douyin_sender")


class RunStopped(Exception):
    """用户在前端点击「停止」后由 run() 抛出，用于优雅终止本次发送任务（当前好友处理完即退出）。"""
    pass





async def run(

    dry_run: bool = False,

    env_file: str | None = None,

    storage_state_path: str | Path | None = None,

    task_config_path_override: str | Path | None = None,

    stop_event: asyncio.Event | None = None,

) -> int:

    settings = load_settings(

        env_file,

        storage_state_path=storage_state_path,

        task_config_path_override=task_config_path_override,

    )

    task = load_task(settings)

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)

    aliases = build_target_aliases(task.targets)

    _configure_logging(settings.artifacts_dir, aliases)



    if not settings.storage_state and not settings.cookie:

        raise ConfigError("必须配置 DOUYIN_STORAGE_STATE 或 DOUYIN_COOKIE")



    history = History(settings.artifacts_dir / "history.json")

    run_date = history.run_date(task.timezone)

    results: list[TargetResult] = []

    screenshots: list[Path] = []

    fatal_error: Exception | None = None

    try:

        async with open_douyin(settings) as session:

            page = session.page

            trace_saved = False

            try:

                await open_private_messages(page)

            except Exception as exc:

                LOGGER.exception("打开抖音私信页面失败")

                screenshot = await _screenshot(page, settings.artifacts_dir, "login")

                if screenshot:

                    screenshots.append(screenshot)

                if settings.trace and not trace_saved:

                    try:

                        await save_trace(session, _trace_path(settings.artifacts_dir))

                        trace_saved = True

                    except Exception:

                        LOGGER.exception("保存 trace 失败")

                label = "登录检查" if isinstance(exc, (AuthenticationError, RiskControlError)) else "运行检查"

                results.append(TargetResult(target=label, status="failed", error=str(exc)))

                fatal_error = exc



            if fatal_error is None:

                chat = DouyinChat(page, timeout_ms=int(task.target_open_timeout_seconds * 1000))

                # 准备文案轮询序列：先随机打散候选文案，再按好友顺序依次取，保证相邻好友文案不同
                _shuffle_pool = None
                for _t in task.targets:
                    if getattr(_t, "messages", None):
                        _shuffle_pool = list(_t.messages)
                        break
                if _shuffle_pool:
                    random.shuffle(_shuffle_pool)

                for index, target in enumerate(task.targets):
                    # 每个好友开始处理前检测停止信号，置位则优雅终止（不抛异常中断，留给 do_run 捕获）
                    if stop_event is not None and stop_event.is_set():
                        LOGGER.info("用户自行停止，剩余好友未发送")
                        raise RunStopped("用户已手动停止")

                    sent = 0

                    alias = target.name  # 日志直接显示用户填写的好友 ID

                    try:

                        LOGGER.info("开始处理好友: %s", alias)

                        # 按好友顺序从洗牌后的文案池轮询取一条，保证每个好友文案不同
                        msg_text = ""
                        message = None
                        message_index = -1
                        # 干跑验证只检测好友框、找好友，不做发送文案检测（不抽文案、不记录文案#N）
                        if not dry_run and _shuffle_pool:
                            message = _shuffle_pool[index % len(_shuffle_pool)]
                            message_index = _shuffle_pool.index(message)
                            msg_text = message["value"] if isinstance(message, dict) else str(message)

                        # 检测好友框并打开会话；找不到好友框则按 target_open_retries 重试。
                        # 重试逻辑放在这里（而非 open_target 内部），以便每次重试前都能检测停止信号。
                        # 把单个好友的全部处理（打开会话 + 采集火花天数 + 发送）包成一个协程，
                        # 用 asyncio.wait_for 做 90s 总硬超时兜底：任何未被上面显式超时覆盖的
                        # 隐蔽卡死都绝不可能超过 90s，超时则该好友跳过、继续下一个，绝不整体卡死。
                        async def _handle_one() -> int:
                            opened = False
                            open_err: Exception | None = None
                            local_sent = 0
                            # 发送内容：默认取外层已选好的自定义文案；AI 模式下为每个好友实时生成（失败回退自定义）
                            msg = message
                            msg_idx = message_index
                            msg_txt = msg_text
                            if (not dry_run) and getattr(task, "content_mode", "custom") == "ai" and (getattr(task, "ai_config", None) or {}).get("api_key"):
                                try:
                                    _ai_text = await asyncio.to_thread(generate_ai_text, task.ai_config)
                                    if _ai_text and _ai_text.strip():
                                        msg = Message(type="text", content=_ai_text.strip())
                                        msg_idx = -1
                                        msg_txt = _ai_text.strip()
                                except Exception as _e:
                                    LOGGER.warning("好友「%s」AI 文案生成失败，回退到自定义文案: %s", alias, _e)
                            for _att in range(task.target_open_retries + 1):
                                if stop_event is not None and stop_event.is_set():
                                    LOGGER.info("用户自行停止，剩余好友未发送")
                                    raise RunStopped("用户已手动停止")
                                try:
                                    await chat.open_target(target.name, retries=0)
                                    opened = True
                                    break
                                except Exception as _e:
                                    open_err = _e
                                    LOGGER.warning(
                                        "打开好友「%s」会话失败（第 %d/%d 次）: %r",
                                        alias, _att + 1, task.target_open_retries + 1, _e,
                                    )
                                    if _att < task.target_open_retries:
                                        await asyncio.sleep(1_500)
                            if not opened:
                                if open_err is not None:
                                    raise open_err
                                return local_sent

                            # best-effort：采集该好友当前火花天数（用于控制台概览卡片；失败不影响发送）
                            try:
                                _days = await chat.read_spark_days(target.name)
                                if _days:
                                    _save_spark_days(target.name, _days)
                            except Exception:
                                pass

                            # 发送前再次检测停止信号，避免已停止仍发出消息
                            if stop_event is not None and stop_event.is_set():
                                LOGGER.info("用户自行停止，剩余好友未发送")
                                raise RunStopped("用户已手动停止")

                            if not dry_run and msg is not None:
                                message_id = _message_id(msg_idx, msg)
                                key = history.key(task.task_id, run_date, target.name, message_id)
                                if task.prevent_duplicates and history.contains(key):
                                    LOGGER.info(
                                        "跳过当天已处理或结果不确定的消息: %s #%d",
                                        alias,
                                        msg_idx + 1,
                                    )
                                else:
                                    if task.prevent_duplicates:
                                        history.reserve(key)
                                    await verify_login(page, timeout_ms=3_000)
                                    await send_message(page, chat, msg, task.stickers)
                                    if task.prevent_duplicates:
                                        history.mark_success(key)
                                    local_sent += 1
                                    LOGGER.info("文案#%d %s", msg_idx + 1, msg_txt)
                            return local_sent

                        try:
                            sent = await asyncio.wait_for(_handle_one(), timeout=90.0)
                        except asyncio.TimeoutError:
                            LOGGER.error(
                                "好友「%s」处理超过 90s（疑似浏览器卡死），跳过该好友继续下一个",
                                alias,
                            )
                            continue

                        results.append(TargetResult(target=target.name, status="success", sent=sent, target_alias=alias))

                    except (AuthenticationError, RiskControlError) as exc:

                        LOGGER.exception("处理好友时登录状态失效: %s", alias)

                        screenshot = await _screenshot(page, settings.artifacts_dir, alias)

                        if screenshot:

                            screenshots.append(screenshot)

                        if settings.trace and not trace_saved:

                            try:

                                await save_trace(session, _trace_path(settings.artifacts_dir))

                                trace_saved = True

                            except Exception:

                                LOGGER.exception("保存 trace 失败")

                        results.append(TargetResult(target=target.name, status="failed", sent=sent, error=str(exc), target_alias=alias))

                        fatal_error = exc

                        break

                    except Exception as exc:

                        LOGGER.exception("好友处理失败: %s", alias)

                        screenshot = await _screenshot(page, settings.artifacts_dir, alias)

                        if screenshot:

                            screenshots.append(screenshot)

                        if settings.trace and not trace_saved:

                            try:

                                await save_trace(session, _trace_path(settings.artifacts_dir))

                                trace_saved = True

                            except Exception:

                                LOGGER.exception("保存 trace 失败")

                        results.append(TargetResult(target=target.name, status="failed", sent=sent, error=str(exc), target_alias=alias))

                        if not task.continue_on_error:

                            break

                    if index < len(task.targets) - 1 and not dry_run:

                        await asyncio.sleep(random.uniform(task.interval_min, task.interval_max))



            if settings.trace and not trace_saved:

                try:

                    await session.context.tracing.stop()

                except Exception as exc:

                    LOGGER.exception("停止 trace 失败")

                    if fatal_error is None:

                        fatal_error = exc

                        results.append(TargetResult(target="运行收尾", status="failed", error=str(exc)))

    except Exception as exc:

        if fatal_error is None:

            fatal_error = exc

            results.append(TargetResult(target="运行检查", status="failed", error=str(exc)))



    _write_results(settings.artifacts_dir, task.task_id, dry_run, results, aliases)

    await _notify_dingtalk(settings, task.task_id, dry_run, results, screenshots)

    succeeded = sum(result.status == "success" for result in results)

    failed = sum(result.status == "failed" for result in results)

    LOGGER.info("执行结束: 成功 %d，失败 %d", succeeded, failed)

    if fatal_error is not None:

        raise fatal_error

    return 1 if failed else 0





def _save_spark_days(name: str, days: int) -> None:
    """把好友当前火花天数写入 data/spark_days.json（供控制台概览卡片读取）。"""
    try:
        data_dir = Path(__file__).resolve().parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        p = data_dir / "spark_days.json"
        data: dict = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8") or "{}")
            except Exception:
                data = {}
        data[name] = days
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def main() -> int:

    args = _parse_cli_args()

    try:

        settings = load_settings(args.env_file)

        with run_lock(settings.artifacts_dir / "run.lock"):

            return asyncio.run(run(dry_run=args.dry_run, env_file=args.env_file))

    except (ConfigError, AuthenticationError, RiskControlError, SearchBoxNotReadyError, AlreadyRunningError) as exc:

        print(f"错误: {exc}")

        return 2

    except KeyboardInterrupt:

        print("任务已取消")

        return 130





def _parse_cli_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(description="向多个抖音好友发送配置的消息")

    parser.add_argument("--dry-run", action="store_true", help="只验证登录和好友，不发送消息")

    parser.add_argument("--env-file", help="指定 .env 文件路径")

    return parser.parse_args()





def _configure_logging(

    artifacts_dir: Path,

    aliases: dict[str, str] | None = None,

    *,

    label: str | None = None,

    reset: bool = False,

) -> None:

    if reset or not LOGGER.handlers:

        for handler in list(LOGGER.handlers):

            LOGGER.removeHandler(handler)

            if isinstance(handler, logging.FileHandler):

                handler.close()

        LOGGER.setLevel(logging.INFO)

        pattern = "%(asctime)s %(levelname)s %(message)s"

        if label:

            pattern = pattern.replace(" %(message)s", f" [{label}] %(message)s")

        formatter = RedactingFormatter(pattern, aliases=aliases)

        artifacts_dir.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(artifacts_dir / "run.log", encoding="utf-8")

        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()

        stream_handler.setFormatter(formatter)

        LOGGER.addHandler(file_handler)

        LOGGER.addHandler(stream_handler)

        return

    # 已有 handler（多账号模式下 run() 内部会再次调用）：只更新脱敏别名。

    for handler in LOGGER.handlers:

        if isinstance(handler.formatter, RedactingFormatter):

            handler.formatter.aliases = dict(aliases or {})





async def _screenshot(page, artifacts_dir: Path, label: str) -> Path | None:

    safe_label = re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "_", label).strip("_")

    suffix = hashlib.sha1(label.encode("utf-8")).hexdigest()[:6]

    safe_label = f"{safe_label}-{suffix}" if safe_label else f"failure-{suffix}"

    directory = artifacts_dir / "screenshots"

    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_label}.png"

    try:

        await page.screenshot(path=path, full_page=True)

        return path

    except Exception:

        LOGGER.exception("保存截图失败")

        return None





def _write_results(

    artifacts_dir: Path,

    task_id: str,

    dry_run: bool,

    results: list[TargetResult],

    aliases: dict[str, str] | None = None,

) -> None:

    payload = {

        "task_id": task_id,

        "dry_run": dry_run,

        "finished_at": datetime.now().astimezone().isoformat(),

        "results": [_redacted_result(result, aliases) for result in results],

    }

    (artifacts_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")





def _redacted_result(result: TargetResult, aliases: dict[str, str] | None = None) -> dict:

    aliases = dict(aliases or {})

    aliases[result.target] = result.target_alias or aliases.get(result.target, result.target)

    return {

        "target": aliases[result.target],

        "status": result.status,

        "sent": result.sent,

        "error": redact_text(result.error, aliases) if result.error else None,

    }





async def _notify_dingtalk(

    settings: Settings,

    task_id: str,

    dry_run: bool,

    results: list[TargetResult],

    screenshots: list[Path],

) -> None:

    if not settings.dingtalk_webhook or not settings.dingtalk_secret:

        return

    try:

        await send_dingtalk_notification(

            settings.dingtalk_webhook,

            settings.dingtalk_secret,

            task_id,

            dry_run,

            results,

            screenshots,

        )

        LOGGER.info("钉钉通知发送成功")

    except Exception:

        LOGGER.exception("钉钉通知发送失败，不影响本次任务结果")





def _trace_path(artifacts_dir: Path) -> Path:

    return artifacts_dir / "traces" / f"{datetime.now():%Y%m%d-%H%M%S}.zip"





def _message_id(index, message) -> str:

    payload = json.dumps(asdict(message), ensure_ascii=False, sort_keys=True, default=str)

    return f"{index}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"

