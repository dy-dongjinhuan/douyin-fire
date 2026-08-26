from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Locator, Page

from app.selectors import CHAT_PANEL_MARKERS, MESSAGE_INPUTS, SEARCH_INPUTS


LOGGER = logging.getLogger("douyin")


class PageOperationError(RuntimeError):
    pass


RETRY_DELAY_MS = 3_000
# 单个 Playwright 动作（click/fill）的硬超时：避免默认 30s 在抖音持续重绘的页面上
# 无尽等待，把整个 run 协程卡死（之前“搜索到第一个人后卡住”的主因之一）。
SAFE_ACTION_TIMEOUT_MS = 8_000
# inner_text 等同步读 DOM 的硬超时上限，绝不允许它无限等“稳定”。
DOM_READ_TIMEOUT_S = 2.0


class DouyinChat:
    def __init__(
        self,
        page: Page,
        timeout_ms: int = 15_000,
        confirm_timeout_ms: int = 15_000,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout_ms = confirm_timeout_ms

    async def open_target(self, name: str, retries: int = 1) -> None:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await self._open_target_once(name)
                return
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    await self.page.wait_for_timeout(RETRY_DELAY_MS)
        if last_error is not None:
            raise last_error
        raise PageOperationError("打开聊天失败")

    async def _open_target_once(self, name: str) -> None:
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        # 显式短超时：任何一步卡住最多 8s 即抛错，绝不会无限等待。
        await search.click(timeout=SAFE_ACTION_TIMEOUT_MS)
        await search.fill("", timeout=SAFE_ACTION_TIMEOUT_MS)
        await search.fill(name, timeout=SAFE_ACTION_TIMEOUT_MS)
        # 冷启动容错：第一个好友的搜索是页面第一次真实输入，抖音搜索面板异步挂载、
        # 固定 1.5s 往往不够（表现为"排第一的好友永远搜索不到，换第二就能搜到"）。
        # 改为显式等结果容器出现；没出现就补一次输入再等（最多 3 轮，总时长 ≤ ~10s）。
        await self._wait_search_ready(name)

        result = await self._search_result(name)
        if result is None:
            # 全名搜不到：部分好友的名字带备注（如 "Skeleton.（小韩）"），抖音搜索对
            # "英文.英文（中文）" 全名不索引，但括号内昵称（"小韩"）能搜到。自动用候选名再搜。
            for cand in self._search_candidates(name):
                try:
                    await self._search_with(cand, name)
                    return
                except Exception:
                    continue
            raise PageOperationError("搜索不到目标好友")
        await result.click(force=True, timeout=SAFE_ACTION_TIMEOUT_MS)
        await self._confirm_opened(name)

    @staticmethod
    def _search_candidates(name: str) -> list[str]:
        """从带备注的名字里提取可被抖音搜索命中的候选名（去重保序）。"""
        import re as _re
        cands: list[str] = []
        # 括号内昵称："Skeleton.（小韩）" → "小韩"
        m = _re.search(r"[（(]([^（）()]+)[)）]", name)
        if m:
            inner = m.group(1).strip()
            if inner and inner != name:
                cands.append(inner)
        # 括号前主体："Skeleton.（小韩）" → "Skeleton"
        parts = _re.split(r"[.．。][\s]*[（(]", name)
        if len(parts) > 1:
            head = parts[0].strip()
            if head and head != name:
                cands.append(head)
        # 去括号、去符号后的拼接主体
        stripped = _re.sub(r"[（(].*?[)）]", "", name)
        stripped = _re.sub(r"[.．。·\s]", "", stripped)
        if stripped and stripped != name:
            cands.append(stripped)
        seen: set[str] = set()
        out: list[str] = []
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    async def _search_with(self, cand: str, target_name: str) -> None:
        """用候选名重新搜索并点击结果；点击后以原始名确认，防止点错人。"""
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        await search.fill(cand, timeout=SAFE_ACTION_TIMEOUT_MS)
        await self.page.wait_for_timeout(2_000)  # 搜索面板已挂载（热），快速等待即可
        result = await self._search_result(cand)
        if result is None:
            raise PageOperationError(f"候选名「{cand}」搜索不到")
        await result.click(force=True, timeout=SAFE_ACTION_TIMEOUT_MS)
        await self._confirm_opened(target_name)

    async def _wait_search_ready(self, name: str) -> None:
        """等待搜索结果出现（冷启动搜索面板异步挂载 / 首次输入被吞的兜底）。

        实测：热状态下输入后 ~2.5s 出结果；冷启动（页面刚加载第一次输入）面板挂载
        明显更慢，单轮 2.5s 不够。因此每轮先校验输入值是否生效（没生效就重填），
        再等结果容器最多 5s，共 4 轮（总面板等待上限 ~20s）。
        """
        for _round in range(4):
            # 1) 确认搜索框值 == name（防第一次输入被页面事件吞掉），不对就重填
            try:
                box = self.page.locator(SEARCH_INPUTS[0]).first
                val = await asyncio.wait_for(box.evaluate("el => el.value || ''"), timeout=2.0)
                if val != name:
                    await box.fill(name, timeout=SAFE_ACTION_TIMEOUT_MS)
            except Exception:
                pass
            # 2) 搜索面板结果出现
            try:
                panel = self.page.locator('[class*="SearchPanelitem"]').first
                await asyncio.wait_for(panel.wait_for(state="visible", timeout=5_000), timeout=5.5)
                await self.page.wait_for_timeout(800)
                return
            except Exception:
                pass
            # 3) 会话列表出现匹配条目（_search_result 的 fallback 路径）
            try:
                rows = self.page.locator('[data-e2e="conversation-item"]').filter(has_text=name)
                if await asyncio.wait_for(rows.count(), timeout=2.0) > 0:
                    return
            except Exception:
                pass
            await self.page.wait_for_timeout(400)
        # 兜底：补最后一次输入并再等一轮
        try:
            box = self.page.locator(SEARCH_INPUTS[0]).first
            await box.fill(name, timeout=SAFE_ACTION_TIMEOUT_MS)
            await self.page.wait_for_timeout(2_000)
        except Exception:
            pass

    async def _search_result(self, name: str) -> Locator | None:
        # Search mode renders a separate SearchPanel. Its "发消息" action is the
        # correct control; clicking the hidden conversation cache does not mount
        # the composer.
        search_items = self.page.locator('[class*="SearchPanelitem"]').filter(has_text=name)
        for index in range(await search_items.count()):
            item = search_items.nth(index)
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            if await button.count():
                return button

        # The nickname node can be hidden while its conversation row is visible.
        # Locate and click the complete row instead of relying on text visibility.
        row_selectors = (
            '[data-e2e="conversation-item"]',
            '[class*="conversationConversationItem"]',
            '[class*="conversation-item"]',
            '[class*="ConversationItem"]',
        )
        for selector in row_selectors:
            rows = self.page.locator(selector).filter(has_text=name)
            for index in range(await rows.count()):
                row = rows.nth(index)
                try:
                    class_name = await row.get_attribute("class") or ""
                    if "wrapper" in class_name or await row.get_attribute("data-e2e") == "conversation-item":
                        return row
                except Exception:
                    continue

        candidates = [self.page.get_by_text(name, exact=True), self.page.get_by_text(name, exact=False)]
        for candidate_group in candidates:
            count = await candidate_group.count()
            visible: list[Locator] = []
            for index in range(count):
                candidate = candidate_group.nth(index)
                try:
                    if await candidate.is_visible():
                        visible.append(candidate)
                except Exception:
                    continue
            if len(visible) == 1:
                return visible[0]
            if len(visible) > 1:
                return visible[0]

        # Some Douyin builds render the title itself as hidden, but keep a visible
        # ancestor as the actionable result. Find that ancestor from the hidden title.
        hidden_titles = self.page.locator('[class*="conversationConversationItemtitle"]').filter(has_text=name)
        for index in range(await hidden_titles.count()):
            row = hidden_titles.nth(index).locator(
                "xpath=ancestor::*[contains(@class, 'conversationConversationItem')][1]"
            )
            if await row.count() and await row.is_visible():
                return row

        for selector in (f'[title="{_css_escape(name)}"]', f'[aria-label="{_css_escape(name)}"]'):
            candidate = self.page.locator(selector).first
            if await candidate.count() and await candidate.is_visible():
                return candidate
        return None

    async def read_spark_days(self, name: str | None = None, timeout_ms: int = 1500) -> "int | None":
        """从已打开的聊天面板读取火花天数（best-effort，失败返回 None，绝不影响发送）。

        抖音把火花天数渲染为「火焰图标 + 数字」徽标（class 含 Streak，如
        commonStreakstreakContainer / commonStreaknormalText），并非"连续 N 天"文字；
        且聊天页 UI 整体在 Shadow DOM 里——必须用 Playwright 定位器（可穿透 shadow
        root）读 innerText，document.querySelectorAll 穿不透 shadow root 会永远读不到
        （此前卡片⑤一直"暂无数据"的根因）。外层 asyncio.wait_for 硬超时兜底，最坏
        只消耗 timeout_ms，绝不卡住 run。
        """
        try:
            import re as _re
            # 1) 已打开聊天的头部（RightPanelHeader 内含 "嘉昇 164"），按好友名精确定位
            if name:
                try:
                    hdr = self.page.locator('[class*="RightPanelHeader"]').filter(has_text=name).first
                    txt = await asyncio.wait_for(
                        hdr.evaluate("el => (el.innerText || '').trim()"),
                        timeout=timeout_ms / 1000.0,
                    )
                    if txt:
                        m = _re.search(r"(\d+)", txt)
                        if m:
                            return int(m.group(1))
                except Exception:
                    pass
            # 2) 会话列表里该好友条目的火花徽标（Streak 徽标内数字即火花天数）
            if name:
                try:
                    item = self.page.locator('[data-e2e="conversation-item"]').filter(has_text=name).first
                    streak = item.locator('[class*="Streak"]').first
                    txt = await asyncio.wait_for(
                        streak.evaluate("el => (el.innerText || '').trim()"),
                        timeout=timeout_ms / 1000.0,
                    )
                    if txt:
                        m = _re.search(r"(\d+)", txt)
                        if m:
                            return int(m.group(1))
                except Exception:
                    pass
            # 3) 兜底：页面任意 Streak 徽标（通常是当前打开聊天的头部徽标）
            try:
                loc = self.page.locator('[class*="Streak"]').first
                txt = await asyncio.wait_for(
                    loc.evaluate("el => (el.innerText || '').trim()"),
                    timeout=timeout_ms / 1000.0,
                )
                if txt:
                    m = _re.search(r"(\d+)", txt)
                    if m:
                        return int(m.group(1))
            except Exception:
                pass
            return None
        except Exception:
            return None

    async def message_input(self) -> Locator:
        return await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)

    async def _confirm_opened(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms if timeout_ms is not None else self.confirm_timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        attempt = 0
        while True:
            attempt += 1
            last_error = await self._chat_open_error(name)
            if last_error is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                LOGGER.warning("确认好友「%s」会话已打开失败（共尝试 %d 次），放弃", name, attempt)
                raise last_error
            await self.page.wait_for_timeout(500)

    async def _chat_open_error(self, name: str) -> PageOperationError | None:
        for selector in CHAT_PANEL_MARKERS:
            locator = self.page.locator(selector).filter(has_text=name).first
            if await locator.count():
                return None

        composer_visible = await self._composer_visible()
        if composer_visible:
            body_text = ""
            try:
                # 用 evaluate 同步读 DOM，不做 Playwright 自动等待（避免 inner_text 等“稳定”在重绘页面上无限卡）。
                body_text = await asyncio.wait_for(
                    self.page.evaluate(
                        "() => (document.body ? document.body.innerText : '').slice(0, 1000)"
                    ),
                    timeout=DOM_READ_TIMEOUT_S,
                )
                body_text = (body_text or "").replace("\n", " ")
            except Exception:
                body_text = ""
            if name in body_text:
                return None
            text = self.page.get_by_text(name, exact=True)
            for index in range(await text.count()):
                candidate = text.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    class_name = await candidate.get_attribute("class") or ""
                    if "conversationConversationItemtitle" not in class_name:
                        return None
                except Exception:
                    continue
        return PageOperationError(
            f"点击搜索结果后无法确认聊天已打开（输入框: {'有' if composer_visible else '无'}）"
        )

    async def _composer_visible(self) -> bool:
        for selector in MESSAGE_INPUTS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False


async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15_000) -> Locator:
    per_selector = max(500, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except Exception:
            continue
    raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}")


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
