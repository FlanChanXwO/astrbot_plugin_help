"""正则表达式示例生成辅助模块

使用 rstr 库生成更准确的正则表达式示例。
"""

import re

import rstr


def generate_regex_examples(pattern: str, limit: int = 10) -> tuple[list[str], bool]:
    """Generate regex examples using rstr library.

    rstr 库专门用于生成匹配正则表达式的随机字符串，
    比简单的模式匹配更准确和强大。

    Args:
        pattern: 正则表达式模式
        limit: 返回结果数量限制

    Returns:
        (示例列表, 是否完整)
    """
    try:
        compiled = re.compile(pattern)
    except Exception:
        return [], True

    examples = []

    try:
        # 使用 rstr 生成多个示例
        # 限制生成次数避免无限循环
        max_attempts = limit * 3
        seen = set()

        for _ in range(max_attempts):
            if len(examples) >= limit:
                break

            try:
                # rstr.xeger() 生成匹配正则表达式的随机字符串
                example = rstr.xeger(pattern)

                # 验证示例确实匹配
                if not compiled.search(example):
                    continue

                # 去重并限制长度
                if example in seen or len(example) > 100:
                    continue

                seen.add(example)
                examples.append(example)

            except Exception:
                # 如果 rstr 生成失败，回退到简单策略
                continue

    except Exception:
        # 如果 rstr 完全失败，回退到简单策略
        pass

    # 如果 rstr 失败或没有生成足够的示例，使用简单策略补充
    if len(examples) < 3:
        # 策略1：提取模式中的字面文本序列
        literal_sequences = re.findall(r"[一-龥a-zA-Z0-9]{2,}", pattern)
        for seq in literal_sequences[:5]:
            seq = seq.strip()
            if seq and compiled.search(seq) and seq not in examples:
                examples.append(seq)

        # 策略1.5：如果没有提取到足够的示例，尝试构建组合示例
        if len(examples) < 2:
            all_literals = re.findall(r"[一-龥a-zA-Z0-9]+", pattern)
            if len(all_literals) >= 2:
                combined = "".join(all_literals[:3])
                if compiled.search(combined) and combined not in examples:
                    examples.append(combined)
            elif all_literals:
                lit = all_literals[0]
                if compiled.search(lit) and lit not in examples:
                    examples.append(lit)

        # 策略2：为常见的正则模式类型生成示例
        if len(examples) < 3:
            if re.search(r"[一-龥]", pattern):
                chinese_examples = ["测试", "示例", "文本", "内容", "数据"]
                for ex in chinese_examples:
                    if compiled.search(ex) and ex not in examples:
                        examples.append(ex)
                        if len(examples) >= 3:
                            break

            if re.search(r"[a-zA-Z]", pattern) and len(examples) < 3:
                english_examples = ["test", "example", "text", "hello", "world"]
                for ex in english_examples:
                    if compiled.search(ex) and ex not in examples:
                        examples.append(ex)
                        if len(examples) >= 3:
                            break

            if re.search(r"\d", pattern) and len(examples) < 3:
                number_examples = ["123", "456", "789", "0", "1"]
                for ex in number_examples:
                    if compiled.search(ex) and ex not in examples:
                        examples.append(ex)
                        if len(examples) >= 3:
                            break

    # 去重并排序
    ordered = sorted(set(examples), key=lambda x: (len(x), x))

    # 限制数量
    if len(ordered) > limit:
        return ordered[:limit], False
    return ordered[:limit], len(ordered) >= limit


def build_regex_usage_hint(pattern: str, examples: list[str]) -> str:
    """构建正则使用提示"""
    if examples:
        joined = "、".join(f"`{item}`" for item in examples[:3])
        return f"发送形如 {joined} 的文本即可触发该正则规则。"
    return f"发送匹配 `{pattern}` 的文本即可触发该正则规则。"
