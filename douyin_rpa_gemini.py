# -*- coding: utf-8 -*-
"""
抖音网页端 (www.douyin.com) 群聊 RPA 挂机机器人 - 双核桥接增强版
特性：
1. 监控指定抖音群聊（通过 .env 的 TARGET_GROUP 配置）：任何人 @ 触发词即秒级捕获或回复
2. React Fiber 原生节点穿透解析（直接读取组件内部 parsedContent，免疫混淆类名变化，支持最长 20000 字符）
3. 头像中线落点算法 (midX) + 连续气泡身份继承机制（准确区分自己发送与群友发送）
4. 基线 (Baseline) 过滤机制：启动时载入当前可见历史，防止重启后重复触发历史消息
5. 支持 --bridge（事件驱动接入外部 AI 大脑）、独立 OpenAI 兼容模型模式及 --login 首次扫码登录
"""
import os
import sys
import re
import json
import time
import random
import hashlib
from pathlib import Path
from typing import Optional, Set, List, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright, Page
from loguru import logger
import config
from llm_client import LLMClient

RUNTIME_DIR = config.BASE_DIR / "runtime"
CACHE_FILE = config.BASE_DIR / "processed_history.json"
OUTBOX_QUEUE_FILE = config.BASE_DIR / "outbox_queue.txt"
BRIDGE_INBOX_DIR = config.BASE_DIR / "bridge_inbox"
BRIDGE_OUTBOX_DIR = config.BASE_DIR / "bridge_outbox"


def _build_mention_pattern(aliases: List[str]) -> re.Pattern:
    escaped = [re.escape(a) for a in aliases if a.strip()]
    if not escaped:
        escaped = ["gemini"]
    return re.compile(r"@\s*(?:" + "|".join(escaped) + r")", re.IGNORECASE)


class DouyinGroupRPA:
    def __init__(self, llm_client: Optional[LLMClient] = None, bridge_mode: bool = False):
        self.target_url = config.DOUYIN_URL
        self.target_group = config.TARGET_GROUP
        self.mention_pattern = _build_mention_pattern(config.BOT_MENTION_ALIASES)
        self.user_data_dir = Path(config.USER_DATA_DIR).resolve()
        self.headless = config.HEADLESS
        self.bridge_mode = bridge_mode
        self.llm = llm_client or (None if bridge_mode else LLMClient())
        self.processed: Set[str] = self._load_cache()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if self.bridge_mode:
            BRIDGE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
            BRIDGE_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    def _load_cache(self) -> Set[str]:
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                return set(data)
            except Exception as e:
                logger.warning(f"读取历史缓存出错: {e}")
        return set()

    def _save_cache(self):
        try:
            cache_list = list(self.processed)[-3000:]
            CACHE_FILE.write_text(json.dumps(cache_list, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _get_sig(self, chat_name: str, sender: str, msg: str) -> str:
        return hashlib.md5(f"{chat_name}:{sender}:{msg}".strip().encode("utf-8")).hexdigest()

    def _ensure_in_target_group(self, page: Page) -> bool:
        """确保停留在目标群聊窗口内"""
        try:
            in_target = page.evaluate(
                """(groupName) => {
                    var input = document.querySelector('div[contenteditable="true"]');
                    var header = document.querySelector('[class*="headerTitle"], [class*="ChatTitle"], [class*="header-title"], [class*="Title"]');
                    var isTitleMatch = header && header.innerText && header.innerText.includes(groupName);
                    var isBodyMatch = document.body && document.body.innerText.includes(groupName);
                    return input !== null && (isTitleMatch || isBodyMatch);
                }""",
                self.target_group,
            )
            if in_target:
                return True
        except Exception:
            pass

        # 若在其他会话中（有输入框但非目标群聊），点击返回回到会话列表
        try:
            if page.query_selector('div[contenteditable="true"]'):
                logger.info("当前处于其他会话中，点击返回会话列表...")
                page.evaluate("""() => {
                    var svg = document.querySelector('[class*="header"] svg, [class*="back"] svg, [class*="Title"] svg');
                    if (svg && svg.parentElement) svg.parentElement.click();
                }""")
                page.wait_for_timeout(2000)
        except Exception:
            pass

        # 检查消息抽屉是否展开，未展开则点击顶部导航【消息】
        conv_item = page.locator('[data-e2e="conversation-item"]').first
        chat_box = page.locator('div[contenteditable="true"]').first
        is_visible = (conv_item.count() > 0 and conv_item.is_visible()) or (
            chat_box.count() > 0 and chat_box.is_visible()
        )

        if not is_visible:
            logger.info("右侧消息抽屉未展开，点击顶部导航【消息】按钮...")
            btn = page.locator('header p:text-is("消息")').first
            if btn.count() > 0:
                btn.click()
                page.wait_for_timeout(2500)

        for _ in range(10):
            ci = page.locator('[data-e2e="conversation-item"]').first
            if ci.count() > 0 and ci.is_visible():
                break
            page.wait_for_timeout(1000)

        logger.info(f"定位并进入【{self.target_group}】...")
        try:
            target_item = page.locator('[data-e2e="conversation-item"]').filter(has_text=self.target_group).first
            if target_item.count() > 0:
                target_item.click(timeout=8000)
                page.wait_for_timeout(2500)
            else:
                logger.warning(f"未在会话列表中找到群聊【{self.target_group}】")
        except Exception as e:
            logger.warning(f"点击群聊异常: {e}")

        try:
            return page.query_selector('div[contenteditable="true"]') is not None
        except Exception:
            return False

    def _extract_messages_via_fiber(self, page: Page) -> List[Dict[str, Any]]:
        """通过 React Fiber 穿透解析群聊消息对象，结合头像 X 坐标落点算法区分发送者"""
        return page.evaluate(r"""() => {
            var listNode = document.querySelector('[class*="messageMessageList"]')
              || document.querySelector('[class*="componentsRightPanel"]')
              || document.body;
            var lb = listNode.getBoundingClientRect();
            var mid = lb.left + lb.width / 2;

            var nodes = Array.prototype.slice.call(
                document.querySelectorAll('[class*="messageMessageBox"], [class*="MessageItemTextcontainer"], [data-e2e="msg-item-content"]')
            );
            var out = [], seenObj = new WeakSet();

            nodes.forEach(function(n) {
                var fk = Object.keys(n).find(function(k) { return k.indexOf('__reactFiber$') === 0; });
                var found = null;
                if (fk) {
                    var f = n[fk], d = 0;
                    while (f && d < 30) {
                        var p = f.memoizedProps;
                        if (p && p.message && typeof p.message === 'object' && p.message.parsedContent) {
                            found = p.message;
                            break;
                        }
                        f = f.return;
                        d++;
                    }
                }
                if (!found || seenObj.has(found)) return;
                seenObj.add(found);

                var r = n.getBoundingClientRect(), side = '';
                var im = Array.prototype.slice.call(n.querySelectorAll('img')).filter(function(img) {
                    var b = img.getBoundingClientRect();
                    if (b.width < 24 || b.width > 56) return false;
                    var cy = b.top + b.height / 2;
                    return cy >= r.top - 12 && cy <= r.bottom + 12;
                })[0];
                if (im) {
                    var ib = im.getBoundingClientRect();
                    side = ((ib.left + ib.width / 2) < mid) ? 'them' : 'me';
                }

                var pc = found.parsedContent || {};
                var text = String(pc.text || pc.content_title || n.innerText || '').replace(/\s+/g, ' ').trim();
                if (text && text.length < 20000 && !['下载客户端', '发送'].includes(text)) {
                    out.push({
                        own: side,
                        text: text,
                        sender: found.senderNickname || found.sender || '',
                        awe: pc.aweType || 0,
                        itemId: found.itemId ? String(found.itemId) : '',
                        isFromMe: found.isFromMe || (n.className && n.className.includes('FromMe')),
                        y: Math.round(r.top)
                    });
                }
            });

            var carry = '';
            for (var i = out.length - 1; i >= 0; i--) {
                if (out[i].own) carry = out[i].own;
                out[i].from = out[i].own || carry || (out[i].isFromMe ? 'me' : 'them');
            }
            return out;
        }""")

    def _send_reply(self, page: Page, reply_text: str) -> bool:
        """单行化注入输入框并回车发送，避免换行符触发提前提交或空白消息拦截"""
        input_box = page.query_selector('div[contenteditable="true"]')
        if not input_box:
            return False

        clean_text = re.sub(r"[\r\n]+", " ", reply_text).strip()
        if not clean_text:
            return False

        input_box.click()
        time.sleep(0.15)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        time.sleep(0.1)

        input_box.evaluate("(el, text) => { el.focus(); document.execCommand('insertText', false, text); }", clean_text)
        if not input_box.inner_text().strip():
            input_box.fill(clean_text)
        time.sleep(0.35)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        return True

    def _check_and_reply_group(self, page: Page) -> bool:
        """扫描群聊新消息，捕获 @ 召唤并投递收件箱或触发独立回复"""
        input_box = page.query_selector('div[contenteditable="true"]')
        if not input_box:
            return False

        chat_data = self._extract_messages_via_fiber(page)
        if not chat_data:
            return False

        chat_data.sort(key=lambda item: item["y"])
        replied = False

        for bubble in chat_data:
            text = bubble.get("text", "")
            sender = bubble.get("sender", "")
            from_who = bubble.get("from", "")
            sig = self._get_sig(self.target_group, sender, text)

            if sig in self.processed:
                continue

            self.processed.add(sig)

            if from_who == "me":
                continue

            logger.info(f"[群聊动态] 来自【{sender or '群友'}】: '{text}'")

            if not self.mention_pattern.search(text):
                continue

            logger.info(f"🎯 [捕获 @ 召唤] 来自【{sender or '群友'}】: '{text}'")

            clean_question = self.mention_pattern.sub("", text).strip()
            clean_question = re.sub(r"^[，,：:\s]+", "", clean_question).strip()
            if not clean_question:
                clean_question = "（你在群里 @ 了机器人，主动打了个招呼）"

            if self.bridge_mode:
                recent_context = [
                    {
                        "sender": m.get("sender", ""),
                        "from": m.get("from", ""),
                        "text": m.get("text", ""),
                    }
                    for m in chat_data[-8:]
                ]
                payload = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "group": self.target_group,
                    "sender": sender or "群友",
                    "raw_text": text,
                    "question": clean_question,
                    "recent_context": recent_context,
                }
                fname = f"msg_{int(time.time() * 1000)}.json"
                tmp_path = BRIDGE_INBOX_DIR / f".{fname}.tmp"
                final_path = BRIDGE_INBOX_DIR / fname
                tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp_path, final_path)
                self._save_cache()
                logger.success(f"📨 [Bridge Mode] 已投递 @ 提问至收件箱 ({fname}): '{clean_question}'")
                replied = True
                break

            logger.info(f"🧠 独立模型正在思考群友【{sender or '群友'}】提问: '{clean_question}'")
            session_id = f"group_{sender or 'member'}"
            reply_text = self.llm.generate_reply(session_id, clean_question) if self.llm else ""
            if not reply_text:
                reply_text = "收到召唤啦！有什么需要帮忙的吗？"

            if self._send_reply(page, reply_text):
                self._save_cache()
                page.screenshot(path=str(RUNTIME_DIR / "last_reply.png"))
                logger.success(f"✔ 【{self.target_group}】智能回答已送达: {reply_text}")
                replied = True
                break

        return replied

    def _check_outbox_queue(self, page: Page) -> bool:
        """消费 bridge_outbox/*.txt 与 outbox_queue.txt 发送回复"""
        sent_any = False

        if BRIDGE_OUTBOX_DIR.exists():
            for f in sorted(BRIDGE_OUTBOX_DIR.glob("*.txt")):
                try:
                    content = f.read_text(encoding="utf-8").strip()
                    if content:
                        lines = [line.strip() for line in content.splitlines() if line.strip()]
                        for text in lines:
                            logger.info(f"📤 [Bridge Outbox] 发送至【{self.target_group}】: {text}")
                            self._send_reply(page, text)
                            page.screenshot(path=str(RUNTIME_DIR / "last_reply.png"))
                            logger.success(f"✔ [Bridge] 回复已成功送达: {text}")
                            sent_any = True
                            time.sleep(0.8)
                    f.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"处理 bridge_outbox 文件 {f.name} 异常: {e}")

        if not OUTBOX_QUEUE_FILE.exists():
            return sent_any
        try:
            content = OUTBOX_QUEUE_FILE.read_text(encoding="utf-8").strip()
            if not content:
                OUTBOX_QUEUE_FILE.unlink(missing_ok=True)
                return sent_any

            lines = [line.strip() for line in content.splitlines() if line.strip()]
            for text in lines:
                logger.info(f"正在发送队列消息至【{self.target_group}】: {text}")
                self._send_reply(page, text)
                page.screenshot(path=str(RUNTIME_DIR / "last_declaration.png"))
                logger.success(f"✔ 成功发送队列消息: {text}")
                sent_any = True
                time.sleep(1.0)

            OUTBOX_QUEUE_FILE.unlink(missing_ok=True)
            return sent_any
        except Exception as e:
            logger.warning(f"处理 outbox_queue.txt 异常: {e}")
            return sent_any

    def login_scan(self):
        """启动可视化 Chrome 窗口供首次扫码登录并保存持久化目录"""
        logger.info("打开浏览器供扫码登录，请登录后在终端按回车或关闭浏览器...")
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                channel="chrome",
                headless=False,
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.goto(self.target_url, wait_until="domcontentloaded", timeout=30000)
            input("完成抖音网页扫码登录（可先手动打开消息抽屉确认）后，按 Enter 键保存登录态并退出...")
            ctx.close()
        logger.success(f"登录态已保存至: {self.user_data_dir}")

    def run_loop(self):
        """守护主循环"""
        logger.info(f"=== 启动抖音【{self.target_group}】群聊实时挂机守护系统 ===")
        logger.info(f"@ 触发词列表: {config.BOT_MENTION_ALIASES}")
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                channel="chrome",
                headless=self.headless,
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            logger.info(f"正在加载抖音页面: {self.target_url}")
            try:
                page.goto(self.target_url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                logger.warning(f"页面导航提示: {e}")
            page.wait_for_timeout(3500)

            self._ensure_in_target_group(page)

            page.wait_for_timeout(2500)
            initial_msgs = self._extract_messages_via_fiber(page)
            for m in initial_msgs:
                self.processed.add(self._get_sig(self.target_group, m.get("sender", ""), m.get("text", "")))
            self._save_cache()
            logger.success(f"已基准化【{self.target_group}】历史消息 {len(initial_msgs)} 条，群聊 @ 监听已开启！")

            cycle = 0
            while True:
                cycle += 1
                try:
                    if not page.query_selector('div[contenteditable="true"]'):
                        self._ensure_in_target_group(page)

                    self._check_outbox_queue(page)

                    replied = self._check_and_reply_group(page)
                    if not replied and cycle % 30 == 0:
                        logger.info(f"[Heartbeat #{cycle}] 正在实时监听【{self.target_group}】@ 消息...")
                except Exception as ex:
                    logger.warning(f"监听轮询异常: {ex}")

                time.sleep(random.uniform(config.POLL_INTERVAL_MIN, config.POLL_INTERVAL_MAX))


if __name__ == "__main__":
    if "--login" in sys.argv:
        DouyinGroupRPA(bridge_mode=True).login_scan()
    else:
        bridge = "--bridge" in sys.argv
        mode_label = "本尊直连桥接模式 (Bridge)" if bridge else "独立大模型模式"
        logger.info(f"运行模式: {mode_label}")
        rpa = DouyinGroupRPA(bridge_mode=bridge)
        rpa.run_loop()
