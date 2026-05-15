"""Tests for regex example generation.

测试正则表达式示例生成功能，包括:
- 锚点处理 (^ 和 $)
- 字符类处理 ([abc], [^abc])
- 重复模式 (*, +, ?, {n,m})
- 复杂模式组合
"""

from __future__ import annotations

import re
import warnings

# 抑制 sre_parse 的废弃警告
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import sre_parse

import pytest

# 直接导入被测试的模块（不依赖 AstrBot）
# 复制核心逻辑进行独立测试


class RegexExampleGenerator:
    """独立测试用的正则示例生成器（复制自 command_index.py 的核心逻辑）."""

    def __init__(self, example_limit: int = 5):
        self._regex_example_limit = example_limit

    def _render_category(self, category) -> list[str]:
        """渲染类别."""
        if category == sre_parse.CATEGORY_DIGIT:
            return ["1", "7"]
        if category == sre_parse.CATEGORY_WORD:
            return ["a", "abc"]
        if category == sre_parse.CATEGORY_SPACE:
            return [" "]
        return ["x"]

    def _render_charset(self, charset, limit: int):
        """渲染字符集，改进取反字符类的处理."""
        values = []
        negated = charset and charset[0][0] is sre_parse.NEGATE
        complete = True

        if negated:
            excluded_chars = set()
            for op, arg in charset[1:]:  # 跳过 NEGATE
                if op is sre_parse.LITERAL:
                    excluded_chars.add(chr(arg))
                elif op is sre_parse.RANGE:
                    start, end = arg
                    for code in range(start, min(end + 1, start + 10)):
                        excluded_chars.add(chr(code))
                elif op is sre_parse.CATEGORY:
                    category_chars = self._render_category(arg)
                    excluded_chars.update(category_chars)

            candidates = []
            test_chars = (
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789"
                "测试示例中文"
            )
            for char in test_chars:
                if char not in excluded_chars:
                    candidates.append(char)
                if len(candidates) >= limit:
                    break

            if not candidates:
                candidates = [",", ".", "!", "@", "#"][:limit]

            ordered = sorted(set(candidates), key=lambda value: (len(value), value))
            return ordered[:limit], len(ordered) <= limit

        for op, arg in charset:
            if op is sre_parse.LITERAL:
                values.append(chr(arg))
            elif op is sre_parse.RANGE:
                start, end = arg
                range_size = min(end - start + 1, 5)
                values.extend([chr(start + i) for i in range(range_size)])
                if end - start > 4:
                    complete = False
            elif op is sre_parse.CATEGORY:
                values.extend(self._render_category(arg))
                complete = False
            else:
                complete = False

        ordered = sorted(
            {v for v in values if v}, key=lambda value: (len(value), value)
        )
        if not ordered:
            return ["x"], False
        truncated = len(ordered) > limit
        return ordered[:limit], complete and not truncated

    def _render_token(self, op, arg, limit: int):
        """渲染单个 token."""
        if op is sre_parse.LITERAL:
            return [chr(arg)], True
        if op is sre_parse.SUBPATTERN:
            return self._expand_tokens(arg[-1], limit)
        if op is sre_parse.BRANCH:
            results = []
            complete = True
            _, branches = arg
            for branch in branches:
                branch_results, branch_complete = self._expand_tokens(branch, limit)
                results.extend(branch_results)
                complete = complete and branch_complete
            ordered = sorted(set(results), key=lambda value: (len(value), value))
            truncated = len(ordered) > limit
            return ordered[:limit], complete and not truncated
        if op is sre_parse.MAX_REPEAT:
            min_repeat, max_repeat, subpattern = arg
            repeat_complete = max_repeat != sre_parse.MAXREPEAT
            if max_repeat == sre_parse.MAXREPEAT:
                max_repeat = min_repeat + 2
            elif max_repeat - min_repeat > 2:
                repeat_complete = False
                max_repeat = min_repeat + 2
            base, base_complete = self._expand_tokens(subpattern, limit)
            base = base or [""]
            counts = list(range(min_repeat, max_repeat + 1))
            results = []
            for count in counts:
                for unit in base[: max(1, limit)]:
                    results.append(unit * count)
            ordered = sorted(set(results), key=lambda value: (len(value), value))
            truncated = len(ordered) > limit
            return ordered[:limit], repeat_complete and base_complete and not truncated
        if op is sre_parse.IN:
            return self._render_charset(arg, limit)
        if op is sre_parse.ANY:
            values = ["a", "x", "1"]
            return values[:limit], len(values) <= limit
        if op is sre_parse.CATEGORY:
            values = self._render_category(arg)
            return values[:limit], True
        if op is sre_parse.AT:
            return [""], True
        if op is sre_parse.ASSERT:
            _, assert_subpattern = arg
            return self._expand_tokens(assert_subpattern, limit)
        if op is sre_parse.ASSERT_NOT:
            return [""], False
        if op is sre_parse.GROUPREF:
            return ["x"], False
        return [""], False

    def _expand_tokens(self, tokens, limit: int):
        """展开正则 token."""
        results = [""]
        complete = True

        for op, arg in tokens:
            if op is sre_parse.AT:
                continue

            pieces, pieces_complete = self._render_token(op, arg, limit)
            complete = complete and pieces_complete

            if not pieces:
                continue

            combined = []
            overflow = False
            for prefix in results:
                for piece in pieces:
                    combined.append(prefix + piece)
                    if len(combined) > limit * 3:
                        overflow = True
                        break
                if overflow:
                    break

            if overflow:
                complete = False
                results = combined[: limit * 3]
                break
            results = combined or results

        return results[: limit * 3], complete

    def _generate_fallback_examples(
        self, pattern: str, compiled: re.Pattern, limit: int
    ) -> list[str]:
        """生成回退示例."""
        examples = []

        literal_sequences = re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]{2,}", pattern)
        for seq in literal_sequences[:3]:
            if compiled.search(seq):
                examples.append(seq)

        if not examples:
            keywords = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}", pattern)
            for kw in keywords[:3]:
                if compiled.search(kw):
                    examples.append(kw)

        if not examples and len(pattern) <= 20:
            simple_tests = ["test", "abc", "123", "hello"]
            for test in simple_tests:
                if compiled.search(test):
                    examples.append(test)
                    if len(examples) >= 2:
                        break

        return examples[:limit]

    def generate_examples(self, pattern: str) -> tuple[list[str], bool]:
        """生成正则示例."""
        try:
            tokens = sre_parse.parse(pattern)
            compiled = re.compile(pattern)
        except Exception:
            return [], True

        has_start_anchor = pattern.startswith("^")
        has_end_anchor = pattern.endswith("$") and not pattern.endswith(r"\$")

        examples, complete = self._expand_tokens(
            tokens.data,
            limit=max(self._regex_example_limit * 2, 1),
        )

        valid_examples = []
        for ex in examples:
            if not ex:
                continue
            try:
                if compiled.search(ex):
                    if has_start_anchor:
                        match = compiled.match(ex)
                        if not match:
                            continue
                    if has_end_anchor:
                        match = compiled.search(ex)
                        if not match or match.end() != len(ex):
                            continue
                    valid_examples.append(ex)
            except re.error:
                continue

        ordered = sorted(set(valid_examples), key=lambda value: (len(value), value))

        if len(ordered) < 2 and pattern:
            fallback = self._generate_fallback_examples(
                pattern, compiled, self._regex_example_limit
            )
            ordered = sorted(
                set(ordered + fallback), key=lambda value: (len(value), value)
            )

        if len(ordered) > self._regex_example_limit:
            return ordered[: self._regex_example_limit], True
        return ordered[: self._regex_example_limit], not complete


class TestRegexExampleGeneration:
    """测试正则示例生成."""

    @pytest.fixture
    def generator(self):
        """提供示例生成器."""
        return RegexExampleGenerator(example_limit=5)

    def test_simple_literal(self, generator):
        """测试简单字面量."""
        examples, complete = generator.generate_examples(r"hello")
        assert "hello" in examples

    def test_anchor_start(self, generator):
        """测试开头锚点 ^."""
        examples, complete = generator.generate_examples(r"^test")
        # 所有示例应该以 "test" 开头
        for ex in examples:
            assert ex.startswith("test"), f"示例 '{ex}' 没有以 'test' 开头"

    def test_anchor_end(self, generator):
        """测试结尾锚点 $."""
        examples, complete = generator.generate_examples(r"world$")
        # 所有示例应该以 "world" 结尾
        for ex in examples:
            assert ex.endswith("world"), f"示例 '{ex}' 没有以 'world' 结尾"

    def test_anchor_both(self, generator):
        """测试双锚点 ^...$."""
        examples, complete = generator.generate_examples(r"^exact$")
        # 示例应该完全匹配 "exact"
        assert "exact" in examples

    def test_char_class(self, generator):
        """测试字符类 [a-z]."""
        examples, complete = generator.generate_examples(r"[a-z]+")
        assert len(examples) > 0
        for ex in examples:
            assert ex.isalpha(), f"示例 '{ex}' 不是纯字母"
            assert ex.islower(), f"示例 '{ex}' 不是小写"

    def test_negated_char_class(self, generator):
        """测试取反字符类 [^abc]."""
        examples, complete = generator.generate_examples(r"[^abc]+")
        assert len(examples) > 0
        for ex in examples:
            assert "a" not in ex, f"示例 '{ex}' 包含被排除的字符 'a'"
            assert "b" not in ex, f"示例 '{ex}' 包含被排除的字符 'b'"
            assert "c" not in ex, f"示例 '{ex}' 包含被排除的字符 'c'"

    def test_digit_class(self, generator):
        """测试数字类 \d."""
        examples, complete = generator.generate_examples(r"\d{2,4}")
        assert len(examples) > 0
        for ex in examples:
            assert ex.isdigit(), f"示例 '{ex}' 不是纯数字"
            assert 2 <= len(ex) <= 4, f"示例 '{ex}' 长度不在 2-4 之间"

    def test_word_class(self, generator):
        """测试单词类 \w."""
        examples, complete = generator.generate_examples(r"\w+")
        assert len(examples) > 0

    def test_space_class(self, generator):
        """测试空白类 \s."""
        examples, complete = generator.generate_examples(r"\s+")
        assert len(examples) > 0
        for ex in examples:
            assert all(c.isspace() for c in ex), f"示例 '{ex}' 包含非空白字符"

    def test_alternation(self, generator):
        """测试分支 |."""
        examples, complete = generator.generate_examples(r"hello|world")
        assert len(examples) >= 2
        # 应该包含两种可能
        has_hello = any("hello" in ex for ex in examples)
        has_world = any("world" in ex for ex in examples)
        assert has_hello or has_world, "应该包含 'hello' 或 'world'"

    def test_optional(self, generator):
        """测试可选 ?."""
        examples, complete = generator.generate_examples(r"colou?r")
        assert len(examples) > 0

    def test_group_repeat(self, generator):
        """测试分组重复 ()."""
        examples, complete = generator.generate_examples(r"(abc)+")
        assert len(examples) > 0

    def test_complex_chinese_pattern(self, generator):
        """测试复杂中文模式（如色图命令）."""
        pattern = r"^/?(来\s*(.*?)(份|个|张|点))(.*?)(?:福利|色|瑟|涩|塞)?图$"
        examples, complete = generator.generate_examples(pattern)

        # 验证生成的示例能真正匹配
        compiled = re.compile(pattern)
        for ex in examples:
            match = compiled.search(ex)
            assert match is not None, f"示例 '{ex}' 不匹配模式"
            # 由于有 $ 锚点，验证完整匹配
            assert match.end() == len(ex), f"示例 '{ex}' 没有完整匹配（$锚点）"

    def test_examples_actually_match(self, generator):
        """测试所有生成的示例都能真正匹配原模式."""
        patterns = [
            r"^hello$",
            r"^test",
            r"world$",
            r"[a-z]+",
            r"\d{2,4}",
            r"hello|world",
            r"(abc)+",
            r"^来.*色图$",
        ]

        for pattern in patterns:
            examples, _ = generator.generate_examples(pattern)
            compiled = re.compile(pattern)

            for ex in examples:
                assert compiled.search(ex) is not None, (
                    f"模式 '{pattern}' 的示例 '{ex}' 无法匹配"
                )

    def test_empty_pattern(self, generator):
        """测试空模式."""
        examples, complete = generator.generate_examples("")
        assert examples == []

    def test_invalid_pattern(self, generator):
        """测试无效模式."""
        examples, complete = generator.generate_examples(r"[invalid")
        # 应该返回空列表而不抛出异常
        assert examples == []

    def test_very_complex_pattern(self, generator):
        """测试非常复杂的模式."""
        pattern = r"^\+?(\d{1,3})?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$"
        examples, complete = generator.generate_examples(pattern)
        # 复杂模式可能生成失败，但至少不应该崩溃
        assert isinstance(examples, list)


class TestRegexAnchorValidation:
    """专门测试锚点验证逻辑."""

    def test_start_anchor_match_validation(self):
        """测试开头锚点的 match 验证."""
        pattern = r"^start"
        compiled = re.compile(pattern)

        # 应该匹配
        assert compiled.match("start here")
        assert compiled.search("start here")

        # 不应该匹配（不以 start 开头）
        assert compiled.match("not start") is None
        # ^start 中的^表示"以start开头"，match从开头匹配，所以"not start"不匹配
        # search会在字符串中寻找子串，但因为^锚点，也只能从开头匹配
        # 所以search("not start")也返回None

    def test_end_anchor_search_validation(self):
        """测试结尾锚点的 search 验证."""
        pattern = r"end$"
        compiled = re.compile(pattern)

        # 应该匹配
        match = compiled.search("the end")
        assert match is not None
        assert match.end() == len("the end")

        # "end here"中end不在末尾，$锚点要求end必须出现在结尾，所以不匹配

    def test_both_anchors_full_match(self):
        """测试双锚点的完整匹配."""
        pattern = r"^exact$"
        compiled = re.compile(pattern)

        assert compiled.search("exact")
        match = compiled.search("exact")
        assert match.start() == 0
        assert match.end() == len("exact")

        assert compiled.search("not exact") is None
        assert compiled.search("exactly") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
