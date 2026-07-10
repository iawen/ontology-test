# ============================================================
# 上下文压缩 Agent
# ============================================================


class ContextCompressorAgent:
    """
    压缩上下文，防止超出模型限制。

    契约：
      输入: context: str, limit: int
      输出: compressed_context: str
    """

    HARD_LIMIT = 20000

    async def compress(self, context: str, limit: int | None = None) -> str:
        limit = limit or self.HARD_LIMIT
        if len(context) <= limit:
            return context
        lines = context.splitlines()
        head_limit = int(limit * 0.75)
        tail_limit = int(limit * 0.25)
        head_lines = []
        head_size = 0
        for line in lines:
            if head_size + len(line) + 1 > head_limit:
                break
            head_lines.append(line)
            head_size += len(line) + 1

        tail_lines = []
        tail_size = 0
        for line in reversed(lines):
            if tail_size + len(line) + 1 > tail_limit:
                break
            tail_lines.append(line)
            tail_size += len(line) + 1
        head = "\n".join(head_lines)
        tail = "\n".join(reversed(tail_lines))
        return head + "\n\n[...上下文已压缩...]\n\n" + tail
