#!/usr/bin/env python
"""Help Plugin Test Runner

跨平台测试运行器，无需 pytest。
Usage:
    python run_tests.py              # 运行所有测试
    python run_tests.py -v           # 详细输出
"""

from __future__ import annotations

import argparse
import re
import sys
import warnings

# 抑制 sre_parse 的废弃警告
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import sre_parse


# =============================================================================
# 测试类（从 command_index.py 复制的核心逻辑）
# =============================================================================


class RegexExampleGenerator:
    """正则示例生成器"""

    def __init__(self, example_limit: int = 5):
        self._regex_example_limit = example_limit

    def _render_category(self, category) -> list[str]:
        """渲染类别"""
        if category == sre_parse.CATEGORY_DIGIT:
            return ["1", "7"]
        if category == sre_parse.CATEGORY_WORD:
            return ["a", "abc"]
        if category == sre_parse.CATEGORY_SPACE:
            return [" "]
        return ["x"]

    def _render_charset(self, charset, limit: int):
        """渲染字符集"""
        values = []
        negated = charset and charset[0][0] is sre_parse.NEGATE
        complete = True

        if negated:
            excluded_chars = set()
            for op, arg in charset[1:]:
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
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
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
        """渲染单个 token"""
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
        """展开正则 token"""
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

    def _generate_fallback_examples(self, pattern, compiled, has_start, has_end):
        """生成回退示例"""
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

        return examples[: self._regex_example_limit]

    def generate_examples(self, pattern: str) -> tuple[list[str], bool]:
        """生成正则示例"""
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

        # 去重并按长度降序排序（最复杂的在前）
        ordered = sorted(
            set(valid_examples), key=lambda value: (len(value), value), reverse=True
        )

        if len(ordered) < 2 and pattern:
            fallback = self._generate_fallback_examples(
                pattern, compiled, has_start_anchor, has_end_anchor
            )
            ordered = sorted(
                set(ordered + fallback), key=lambda value: (len(value), value)
            )

        if len(ordered) > self._regex_example_limit:
            return ordered[: self._regex_example_limit], True
        return ordered[: self._regex_example_limit], not complete


# =============================================================================
# 测试用例
# =============================================================================

TEST_CASES = [
    # (pattern, name, category)
    (r"hello", "simple literal", "basic"),
    (r"^test", "start anchor", "anchors"),
    (r"world$", "end anchor", "anchors"),
    (r"^exact$", "both anchors", "anchors"),
    (r"[a-z]+", "char class", "classes"),
    (r"[^abc]+", "negated class", "classes"),
    (r"\d{2,4}", "digit class", "classes"),
    (r"\w+", "word class", "classes"),
    (r"\s+", "space class", "classes"),
    (r"hello|world", "alternation", "advanced"),
    (r"colou?r", "optional", "advanced"),
    (r"(abc)+", "group repeat", "advanced"),
]


# =============================================================================
# 测试运行器
# =============================================================================


def print_header(text: str) -> None:
    """打印标题"""
    print("=" * 70)
    print(f"    {text}")
    print("=" * 70)


def print_separator() -> None:
    """打印分隔线"""
    print("-" * 70)


def run_tests(verbose: bool = True) -> tuple[int, int]:
    """运行测试，返回 (通过数, 失败数)"""
    generator = RegexExampleGenerator(example_limit=5)

    passed = 0
    failed = 0
    results = []

    for pattern, name, category in TEST_CASES:
        try:
            examples, complete = generator.generate_examples(pattern)
            compiled = re.compile(pattern)

            # 验证所有示例都能匹配
            all_match = True
            for ex in examples:
                if not compiled.search(ex):
                    all_match = False
                    break

            # 验证锚点约束
            anchor_ok = True
            if pattern.startswith("^"):
                for ex in examples:
                    if not compiled.match(ex):
                        anchor_ok = False
                        break
            if pattern.endswith("$") and not pattern.endswith(r"\$"):
                for ex in examples:
                    match = compiled.search(ex)
                    if not match or match.end() != len(ex):
                        anchor_ok = False
                        break

            if all_match and anchor_ok:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1

            results.append(
                {
                    "name": name,
                    "pattern": pattern,
                    "status": status,
                    "examples": examples,
                    "complete": complete,
                }
            )

        except Exception as e:
            failed += 1
            results.append(
                {
                    "name": name,
                    "pattern": pattern,
                    "status": "ERROR",
                    "error": str(e),
                }
            )

    # 打印结果
    if verbose:
        for r in results:
            print(f"\n[{r['status']}] {r['name']}")
            print(f"  Pattern: {r['pattern']!r}")
            if r.get("error"):
                print(f"  Error: {r['error']}")
            else:
                print(f"  Examples: {r['examples']}")
                print(f"  Complete: {r['complete']}")

    return passed, failed


def main() -> int:
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Help Plugin Test Runner",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="显示详细输出",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速模式（仅显示摘要）",
    )

    args = parser.parse_args()

    print_header("Help Plugin Test Runner")
    print(f"Python: {sys.version}")
    print()

    verbose = args.verbose or not args.quick

    # 运行测试
    print("Running tests...")
    print_separator()

    passed, failed = run_tests(verbose=verbose)

    # 打印结果
    print()
    print_separator()
    print(f"\nResults: {passed} passed, {failed} failed")

    if failed == 0:
        print("\nAll tests passed!")
        return 0
    else:
        print(f"\n{failed} test(s) failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
