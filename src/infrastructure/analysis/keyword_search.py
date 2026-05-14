"""关键词搜索器

使用 jieba 分词和多维度搜索找到最相关的命令结果
"""

from __future__ import annotations

import re

from ..utils.logger import get_logger

logger = get_logger()


class KeywordSearcher:
    """关键词搜索器 - 使用 jieba 分词和多维度搜索"""

    # 匹配类型的权重
    WEIGHT_EXACT_MATCH = 100  # 完全匹配
    WEIGHT_PREFIX_MATCH = 80  # 前缀匹配
    WEIGHT_CONTAINS_MATCH = 50  # 包含匹配
    WEIGHT_DESCRIPTION = 30  # 描述匹配
    WEIGHT_ALIAS = 40  # 别名匹配
    WEIGHT_EXAMPLE = 20  # 示例匹配
    WEIGHT_PLUGIN_NAME = 60  # 插件名匹配

    def __init__(self):
        """初始化关键词搜索器"""
        self._word_cache = {}  # 分词缓存
        self._jieba_mode = None  # jieba 分词模式
        self._init_jieba()

    def _init_jieba(self):
        """初始化 jieba 分词器"""
        try:
            import importlib.util

            if importlib.util.find_spec("jieba") is None:
                raise ImportError("jieba not found")

            # 尝试使用精确模式（更准确的分词）
            self._jieba_mode = "search"  # 搜索引擎模式
            logger.info("jieba 分词器初始化成功，使用搜索引擎模式")
        except ImportError:
            logger.warning("jieba 未安装，将使用简单的正则分词")
            self._jieba_mode = None
        except Exception as e:
            logger.warning(f"jieba 初始化失败: {e}，将使用简单的正则分词")
            self._jieba_mode = None

    def tokenize(self, query: str) -> list[str]:
        """分词 - 将搜索关键词拆分成多个词

        使用 jieba 进行中文分词，如果 jieba 不可用则回退到正则分词

        Args:
            query: 搜索关键词

        Returns:
            分词列表
        """
        # 检查缓存
        if query in self._word_cache:
            return self._word_cache[query]

        # 去除首尾空格
        query = query.strip()

        if not query:
            return []

        result = []

        # 使用 jieba 分词（如果可用）
        if self._jieba_mode is not None:
            result = self._tokenize_with_jieba(query)
        else:
            result = self._tokenize_with_regex(query)

        # 去重并保持顺序
        seen = set()
        unique_result = []
        for word in result:
            word = word.strip()
            if word and word not in seen and len(word) >= 1:
                seen.add(word)
                unique_result.append(word)

        # 缓存结果
        self._word_cache[query] = unique_result
        logger.debug(f"Tokenized '{query}' into {unique_result}")
        return unique_result

    def _tokenize_with_jieba(self, query: str) -> list[str]:
        """使用 jieba 进行分词

        Args:
            query: 搜索关键词

        Returns:
            分词列表
        """
        import jieba

        result = []

        # 使用搜索引擎模式分词（适合搜索场景）
        words = jieba.lcut_for_search(query)

        # 添加 jieba 分出的词
        for word in words:
            if word.strip():
                result.append(word.lower())

                # 对于中文词，也添加单字和双字组合
                if re.search(r"[\u4e00-\u9fff]", word):
                    # 添加单字
                    for char in word:
                        if re.search(r"[\u4e00-\u9fff]", char):
                            result.append(char)

                    # 添加双字组合
                    if len(word) >= 2:
                        for i in range(len(word) - 1):
                            result.append(word[i : i + 2])

        return result

    def _tokenize_with_regex(self, query: str) -> list[str]:
        """使用正则表达式进行简单的分词（jieba 不可用时的回退方案）

        Args:
            query: 搜索关键词

        Returns:
            分词列表
        """
        query_lower = query.lower()

        # 先尝试按空格和常见分隔符分割
        words = re.split(r"[\s,，、;；]+", query_lower)

        # 对每个词进一步分割
        result = []
        for word in words:
            if not word:
                continue

            # 如果包含中文，尝试按字分割
            if re.search(r"[\u4e00-\u9fff]", word):
                # 保留原词
                result.append(word)

                # 提取单个字符
                for char in word:
                    result.append(char)

                # 提取子串（2-3个字符）
                for i in range(len(word) - 1):
                    result.append(word[i : i + 2])
                if len(word) >= 3:
                    for i in range(len(word) - 2):
                        result.append(word[i : i + 3])
            else:
                # 英文/数字：保留原词和子词
                result.append(word)

                # 提取子词（例如 "help" -> "help", "elp", "lp", "p"）
                for i in range(len(word)):
                    for j in range(i + 2, min(i + 6, len(word) + 1)):
                        result.append(word[i:j])

        return result

    def calculate_relevance_score(
        self, command: dict, tokens: list[str], original_query: str
    ) -> int:
        """计算命令与搜索词的相关性得分

        Args:
            command: 命令字典
            tokens: 分词列表
            original_query: 原始搜索词

        Returns:
            相关性得分（越高越相关）
        """
        score = 0

        # 安全获取字段，避免 None 值
        cmd_name = (command.get("command") or "").lower()
        plugin_name = (command.get("plugin") or "").lower()
        plugin_display = (command.get("plugin_display_name") or "").lower()
        description = (command.get("description") or "").lower()
        aliases = [(a or "").lower() for a in command.get("aliases") or []]
        examples = [(e or "").lower() for e in command.get("examples") or []]

        # 1. 完全匹配原始查询（最高优先级）
        if original_query.lower() == cmd_name:
            score += self.WEIGHT_EXACT_MATCH
        if original_query.lower() in plugin_name:
            score += self.WEIGHT_EXACT_MATCH
        if original_query.lower() in plugin_display:
            score += self.WEIGHT_EXACT_MATCH

        # 2. 前缀匹配
        if cmd_name.startswith(original_query.lower()):
            score += self.WEIGHT_PREFIX_MATCH
        if plugin_name.startswith(original_query.lower()):
            score += self.WEIGHT_PREFIX_MATCH
        if plugin_display.startswith(original_query.lower()):
            score += self.WEIGHT_PREFIX_MATCH

        # 3. 对每个分词进行匹配
        for token in tokens:
            # 命令名匹配
            if token in cmd_name:
                if cmd_name == token:
                    score += self.WEIGHT_EXACT_MATCH
                elif cmd_name.startswith(token):
                    score += self.WEIGHT_PREFIX_MATCH
                else:
                    score += self.WEIGHT_CONTAINS_MATCH

            # 插件名匹配
            if token in plugin_name:
                score += self.WEIGHT_PLUGIN_NAME
            if token in plugin_display:
                score += self.WEIGHT_PLUGIN_NAME

            # 别名匹配
            for alias in aliases:
                if token in alias:
                    if alias == token:
                        score += self.WEIGHT_EXACT_MATCH
                    elif alias.startswith(token):
                        score += self.WEIGHT_PREFIX_MATCH
                    else:
                        score += self.WEIGHT_ALIAS

            # 描述匹配
            if token in description:
                score += self.WEIGHT_DESCRIPTION

            # 示例匹配
            for example in examples:
                if token in example:
                    score += self.WEIGHT_EXAMPLE

        # 4. 多词匹配的加权（如果多个分词都命中，额外加分）
        match_count = sum(
            1
            for token in tokens
            if token in cmd_name or token in plugin_name or token in plugin_display
        )
        if match_count > 1:
            score += match_count * 10

        return score

    def search_intelligent(
        self,
        commands: dict[str, dict],
        query: str,
        limit: int = 10,
        min_score: int = 20,
    ) -> list[dict]:
        """智能搜索 - 使用分词和多维度匹配

        Args:
            commands: 所有命令字典
            query: 搜索关键词
            limit: 返回结果数量限制
            min_score: 最低相关性得分（低于此分数的结果不会返回）

        Returns:
            按相关性排序的命令列表，每个命令包含额外的 'relevance_score' 字段
        """
        if not query or not query.strip():
            return []

        # 分词
        tokens = self.tokenize(query)

        # 计算每个命令的相关性得分
        scored_commands = []
        for cmd_name, cmd_dict in commands.items():
            score = self.calculate_relevance_score(cmd_dict, tokens, query)

            # 只保留得分高于阈值的结果
            if score >= min_score:
                # 添加得分到命令字典中
                cmd_with_score = cmd_dict.copy()
                cmd_with_score["relevance_score"] = score
                scored_commands.append(cmd_with_score)

        # 按相关性得分排序（得分越高越靠前）
        scored_commands.sort(key=lambda x: x["relevance_score"], reverse=True)

        # 限制返回数量
        results = scored_commands[:limit]

        return results


# 单例实例
_searcher_instance: KeywordSearcher | None = None


def get_keyword_searcher() -> KeywordSearcher:
    """获取关键词搜索器单例

    Returns:
        KeywordSearcher 实例
    """
    global _searcher_instance
    if _searcher_instance is None:
        _searcher_instance = KeywordSearcher()
    return _searcher_instance


def reset_keyword_searcher() -> None:
    """重置关键词搜索器（用于测试）"""
    global _searcher_instance
    _searcher_instance = None
