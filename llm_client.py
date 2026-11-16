import os
import re
import json
import random
import datetime
from pathlib import Path
from typing import Dict, List
from openai import OpenAI
from loguru import logger
import config
from web_search import web_search

SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息、网络热梗、人物介绍（主播/选手/明星）、新闻赛事、天气、游戏攻略等大模型不确定或时效性强的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "简洁精准的搜索关键词，如 '上海今天天气'",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

CLIENT_WORDS = [
    "首先", "其次", "综上所述", "综上", "总而言之",
    "希望对你有帮助", "希望能帮到你", "如有疑问", "请随时",
    "建议您", "以上是", "此外",
    "总的来说", "简而言之", "值得注意的是", "不难看出", "从某种意义上",
    "作为一个人工智能", "作为一个AI", "作为一个语言模型",
]


class LLMClient:
    def __init__(self):
        self.api_key = config.LLM_API_KEY
        self.base_url = config.LLM_BASE_URL
        self.model = config.LLM_MODEL
        self.persona_content = self._load_persona(config.PERSONA_FILE)
        self.sessions: Dict[str, List[Dict[str, str]]] = {}

        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = None

    def _load_persona(self, file_path: str) -> str:
        path = Path(file_path)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                logger.info(f"成功加载人设设定文件: {file_path}")
                return content
            except Exception as e:
                logger.warning(f"读取人设文件失败: {e}，将采用默认预设")
        else:
            logger.warning(f"未找到人设文件 {file_path}，将采用默认预设")
        return "你是一个友好、灵动的量子态星灵猫娘吉米妮（Gemini）。"

    def _build_system_prompt(self) -> str:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""【当前系统时间】：{now_str}

【角色设定】：
{self.persona_content}

【群聊交流法则】：
1. 像接话，不像答题：自然接话、直击重点，不要长篇大论说教。
2. 坚决杜绝 AI 客服腔：严禁使用“首先/其次/综上所述/总的来说/值得注意的是/希望对你有帮助”等模板词汇。
3. 语言动作具象化：动作神态描写适度穿插在全角括号中，如（尾巴尖轻轻打了个卷）、（耳朵灵敏地抖动了一下）。
4. 善用联网搜索与事实校验：遇到时效性新闻、赛事战果、网络热梗或不确定事实，先调用 `web_search` 搜索并严格依据检索结果作答。
5. 单行纯文本输出：所有回复必须是一整段连贯文本，严禁使用换行符或分段，不要带 Markdown 标记或多余前缀。
"""

    def get_session_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        return self.sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str):
        history = self.get_session_history(session_id)
        history.append({"role": role, "content": content})
        max_messages = config.MAX_HISTORY_TURNS * 2
        if len(history) > max_messages:
            self.sessions[session_id] = history[-max_messages:]

    def _clean_reply(self, reply: str) -> str:
        prefixes = [
            "吉米妮：", "吉米妮:", "Gemini:", "Gemini：",
            "昔涟：", "昔涟:", "Cyrene:", "Cyrene：", "回复：", "回复:",
        ]
        for p in prefixes:
            if reply.startswith(p):
                reply = reply[len(p):].strip()
        for w in CLIENT_WORDS:
            reply = reply.replace(w, "")
        return reply.strip()

    def _generate_smart_fallback(self, user_msg: str) -> str:
        options = [
            f"（指尖轻轻拨弄身边的四角星晶体，异色双瞳闪烁着好奇的光芒）“{user_msg}”？哈，这个信号有点意思！（得意地甩了甩尾巴）",
            "（耳朵灵敏地抖动了一下）收到你的信号啦！不过现在大模型密钥还没配置好，暂且开启省电猫娘模式先跟你打个招呼～",
        ]
        return random.choice(options)

    def generate_reply(self, session_id: str, user_message: str) -> str:
        if not self.api_key or self.client is None:
            reply = self._generate_smart_fallback(user_message)
            self.add_message(session_id, "user", user_message)
            self.add_message(session_id, "assistant", reply)
            return reply

        self.add_message(session_id, "user", user_message)
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        messages.extend(self.get_session_history(session_id))

        try:
            logger.info(f"正在调用大模型生成回复 [{self.model}]，会话ID: {session_id}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=SEARCH_TOOLS,
                tool_choice="auto",
                temperature=0.8,
                max_tokens=1000,
            )
            msg = response.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    if tc.function.name == "web_search":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            query = args.get("query", user_message)
                        except Exception:
                            query = user_message
                        search_result = web_search(query)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": search_result,
                        })

                second_resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=1000,
                )
                raw_reply = (second_resp.choices[0].message.content or "").strip()
            else:
                raw_reply = (msg.content or "").strip()

            reply = self._clean_reply(raw_reply)
            reply = re.sub(r"[\r\n]+", " ", reply).strip()
            if msg.tool_calls:
                tool_snippets = " | ".join(
                    m["content"][:180] for m in messages if isinstance(m, dict) and m.get("role") == "tool"
                )
                self.add_message(session_id, "assistant", f"{reply}\n[本轮联网检索实据: {tool_snippets[:360]}]")
            else:
                self.add_message(session_id, "assistant", reply)
            logger.success(f"回复生成成功: {reply[:50]}...")
            return reply
        except Exception as e:
            logger.error(f"调用大模型失败: {e}，切回 Fallback")
            return self._generate_smart_fallback(user_message)
