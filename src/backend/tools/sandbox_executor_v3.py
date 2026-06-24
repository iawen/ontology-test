"""
sandbox_executor_v3.py
融合版安全沙盒执行器 —— 取 V1 安全机制之长 + V2 易用性之长，补两者之短。

改进要点:
    1. [来自V1] 安全内置函数白名单 + 模块导入白名单 + spawn 启动 + pickle 序列化
    2. [来自V2] 代码清洗(markdown围栏剥离) + 执行历史记录 + SandboxResult 对象 + stderr 独立捕获
    3. [新增]     移除 getattr/hasattr 防沙盒逃逸 / terminate+kill 双阶段超时 / AST 预检
    4. [新增]     结果序列化安全检查 / 错误信息智能截断 / dataclass 结果对象
"""

import ast
import io
import logging
import multiprocessing
import pickle
import re
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ======================================================================
# 结果数据类
# ======================================================================

@dataclass
class SandboxResult:
    """沙盒执行结果的结构化对象，替代 V1 的字典和 V2 的手写 __init__。"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Any = None       # 代码中赋给 `result` 变量的值
    error_msg: Optional[str] = None
    final_code: str = ""           # 本次实际执行的代码
    attempt: int = 0               # 第几次尝试 (1-based)


@dataclass
class ExecutionHistory:
    """完整执行历史，包含所有尝试的代码与结果。"""
    attempts: List[SandboxResult] = field(default_factory=list)

    def add(self, result: SandboxResult) -> None:
        self.attempts.append(result)

    @property
    def total_attempts(self) -> int:
        return len(self.attempts)

    def last_result(self) -> Optional[SandboxResult]:
        return self.attempts[-1] if self.attempts else None


# ======================================================================
# 安全配置常量
# ======================================================================

# 数据分析常用安全模块白名单
DEFAULT_ALLOWED_MODULES: List[str] = [
    'pandas', 'numpy', 'math', 'statistics',
    'scipy', 'sklearn', 'matplotlib', 'seaborn',
    'datetime', 'collections', 'itertools', 'functools',
    're', 'json', 'typing', 'operator',
    'decimal', 'fractions', 'hashlib', 'copy',
]

# 安全内置函数白名单（移除了 getattr/hasattr/type 等可被利用做沙盒逃逸的函数）
SAFE_BUILTINS: Dict[str, Any] = {
    # 常量
    'True': True, 'False': False, 'None': None,
    # 类型转换
    'bool': bool, 'int': int, 'float': float, 'str': str,
    'bytes': bytes, 'bytearray': bytearray,
    'list': list, 'tuple': tuple, 'dict': dict,
    'set': set, 'frozenset': frozenset,
    'complex': complex,
    # 数值运算
    'abs': abs, 'divmod': divmod, 'pow': pow, 'round': round,
    'min': min, 'max': max, 'sum': sum,
    'bin': bin, 'oct': oct, 'hex': hex, 'chr': chr, 'ord': ord,
    # 迭代与集合
    'enumerate': enumerate, 'filter': filter, 'map': map,
    'zip': zip, 'reversed': reversed, 'sorted': sorted,
    'iter': iter, 'next': next, 'range': range, 'slice': slice,
    'len': len, 'all': all, 'any': any,
    # 类型检查（仅保留安全的）
    'isinstance': isinstance, 'issubclass': issubclass,
    'hash': hash, 'id': id, 'repr': repr, 'format': format,
    # 输出
    'print': print,
    # 常用异常
    'Exception': Exception, 'ValueError': ValueError,
    'KeyError': KeyError, 'TypeError': TypeError,
    'IndexError': IndexError, 'StopIteration': StopIteration,
    'ArithmeticError': ArithmeticError, 'ZeroDivisionError': ZeroDivisionError,
    'FileNotFoundError': FileNotFoundError, 'AttributeError': AttributeError,
    'RuntimeError': RuntimeError, 'NotImplementedError': NotImplementedError,
    'OverflowError': OverflowError, 'NameError': NameError,
}

# AST 预检禁止的危险节点类型
DANGEROUS_AST_NODES = (
    ast.ImportFrom,   # from xxx import yyy 可能绕过白名单
)


# ======================================================================
# 子进程 Worker（必须在模块顶层，以便 pickle 序列化）
# ======================================================================

def _subprocess_worker(
    code: str,
    data_pickle: bytes,
    allowed_modules: List[str],
    result_queue: multiprocessing.Queue,
) -> None:
    """
    子进程入口：反序列化数据 → 构建受控环境 → 执行代码 → 返回结果。

    设计要点:
        - 使用 pickle 反序列化数据集，确保 spawn 模式下数据可用
        - 构建 safe_builtins 字典替代 __builtins__，限制危险函数
        - 通过 safe_import 拦截模块导入，仅允许白名单模块
        - 独立捕获 stdout / stderr
        - 结果经 pickle 可序列化检查后再放入队列
    """
    # 1. 反序列化数据集
    try:
        datasets = pickle.loads(data_pickle)
    except Exception:
        result_queue.put({
            'success': False,
            'error_msg': f'数据集反序列化失败:\n{traceback.format_exc()[:2000]}',
            'stdout': '', 'stderr': '', 'return_value': None,
        })
        return

    # 2. 构建受控内置函数
    safe_builtins = dict(SAFE_BUILTINS)

    # 3. 白名单导入函数
    import builtins as _builtins
    original_import = _builtins.__import__

    def safe_import(name: str, *args, **kwargs):
        top_module = name.split('.')[0]
        if top_module not in allowed_modules:
            raise ImportError(f"沙盒安全限制: 不允许导入模块 '{name}'，仅允许: {', '.join(allowed_modules)}")
        return original_import(name, *args, **kwargs)

    safe_builtins['__import__'] = safe_import

    # 4. 构建执行命名空间
    exec_globals = {
        '__builtins__': safe_builtins,
        '__name__': '__sandbox__',
    }
    exec_locals = dict(datasets)  # 用户数据集作为局部变量注入

    # 5. 设置 matplotlib 非交互式后端
    try:
        import matplotlib
        matplotlib.use('Agg')
    except Exception:
        pass

    # 6. 重定向标准输出/错误
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    sys.stdout = stdout_buf
    sys.stderr = stderr_buf

    success = True
    return_value = None
    error_msg = None

    try:
        exec(code, exec_globals, exec_locals)

        # 优先获取代码中赋值的 'result' 变量
        return_value = exec_locals.get('result', None)

        # 如果没有 result 变量，尝试从 stdout 获取
        if return_value is None:
            output = stdout_buf.getvalue().strip()
            if output:
                return_value = output

    except Exception:
        success = False
        # 提取简明的错误信息，限制长度避免超出 LLM token 限制
        error_msg = traceback.format_exc()[:3000]
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # 7. 结果序列化安全检查
    serializable_return = None
    if success and return_value is not None:
        try:
            pickle.dumps(return_value)
            serializable_return = return_value
        except Exception:
            # 不可序列化时转为字符串表示
            try:
                serializable_return = str(return_value)[:5000]
            except Exception:
                serializable_return = '<unserializable result>'

    result_queue.put({
        'success': success,
        'return_value': serializable_return,
        'stdout': stdout_buf.getvalue(),
        'stderr': stderr_buf.getvalue(),
        'error_msg': error_msg,
    })


# ======================================================================
# 主沙盒执行器类
# ======================================================================

class SandboxExecutor:
    """
    安全、可重试的代码沙盒执行器，适用于大模型生成的 pandas 数据分析代码。

    融合 V1 的安全机制与 V2 的易用性设计:
        - 安全: 内置函数白名单 + 模块导入白名单 + spawn 隔离 + pickle 序列化
        - 易用: 代码自动清洗 + 执行历史记录 + 结构化结果对象 + stderr 独立捕获
        - 健壮: AST 预检 + 双阶段超时 + 结果序列化检查 + 错误信息截断

    使用示例:
        def llm_fix(code: str, error: str, retry: int) -> str:
            # 调用大模型根据 error 修复 code
            return fixed_code

        executor = SandboxExecutor(
            datasets={"sales": df_sales, "products": df_products},
            llm_fix_func=llm_fix,
            max_retries=3,
            timeout=30,
        )
        result, history = executor.run("result = sales.groupby('product_id').sum()")
        if result.success:
            print(result.return_value)
        else:
            print(result.error_msg)
    """

    def __init__(
        self,
        datasets: Dict[str, Any],
        llm_fix_func: Callable[[str, str, int], str],
        max_retries: int = 3,
        timeout: int = 30,
        allowed_modules: Optional[List[str]] = None,
        terminate_grace_period: float = 2.0,
    ):
        """
        参数:
            datasets: 变量名 → pandas DataFrame 的字典，代码中可直接使用这些变量。
            llm_fix_func: 修复函数，签名为 (当前代码, 错误信息, 重试次数) → 修复后的代码。
            max_retries: 最大自动修复重试次数（首次执行失败后开始重试）。
            timeout: 单次代码执行的最大时长（秒）。
            allowed_modules: 允许导入的顶级模块名列表，默认使用数据科学常用库。
            terminate_grace_period: 超时后先 SIGTERM，等待此秒数后再 SIGKILL。
        """
        self.datasets = datasets
        self.llm_fix_func = llm_fix_func
        self.max_retries = max_retries
        self.timeout = timeout
        self.allowed_modules = allowed_modules or DEFAULT_ALLOWED_MODULES
        self.terminate_grace_period = terminate_grace_period

        # 确保子进程使用 spawn 方式启动，跨平台行为一致且安全
        try:
            multiprocessing.set_start_method('spawn', force=False)
        except RuntimeError:
            pass  # 已经设置过

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run(self, code: str) -> Tuple[SandboxResult, ExecutionHistory]:
        """
        执行代码，失败时自动修复重试。

        参数:
            code: LLM 生成的 Python 代码（支持包含 markdown 围栏）。

        返回:
            (SandboxResult, ExecutionHistory) 元组:
                - SandboxResult: 最终一次执行的结果
                - ExecutionHistory: 所有尝试的完整历史
        """
        history = ExecutionHistory()

        # 清洗代码（剥离 markdown 围栏等）
        current_code = self._clean_code(code)

        for attempt in range(1, self.max_retries + 2):  # 首次执行 + max_retries 次重试
            # AST 预检
            ast_error = self._ast_check(current_code)
            if ast_error:
                result = SandboxResult(
                    success=False,
                    error_msg=ast_error,
                    final_code=current_code,
                    attempt=attempt,
                )
                history.add(result)
                logger.warning("AST 预检失败 (尝试 %d/%d): %s", attempt, self.max_retries + 1, ast_error)
            else:
                # 在子进程中执行
                result = self._execute_in_subprocess(current_code, attempt)
                history.add(result)

                if result.success:
                    logger.info("代码执行成功 (尝试 %d/%d)", attempt, self.max_retries + 1)
                    return result, history

                logger.warning(
                    "代码执行失败 (尝试 %d/%d): %s",
                    attempt, self.max_retries + 1,
                    result.error_msg[:200] if result.error_msg else "未知错误",
                )

            # 如果没有重试机会了，退出
            if attempt > self.max_retries:
                break

            # 调用大模型修复代码
            error_for_llm = result.error_msg or "未知执行错误"
            try:
                current_code = self.llm_fix_func(current_code, error_for_llm, attempt)
                current_code = self._clean_code(current_code)  # 修复后的代码也需要清洗
                logger.info("大模型已返回修复代码，长度: %d", len(current_code))
            except Exception as fix_err:
                logger.error("调用大模型修复函数失败: %s", fix_err)
                break

        # 所有尝试均失败
        return history.last_result() or SandboxResult(
            success=False, error_msg="所有执行尝试均失败", final_code=current_code,
        ), history

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _clean_code(self, code: str) -> str:
        """
        清洗 LLM 输出代码，剔除 markdown 围栏标记。

        处理模式:
            ```python
            code here
            ```
        以及:
            ```
            code here
            ```
        """
        code = code.strip()

        # 优先匹配 ```python ... ```
        match = re.search(r'```python\s*(.*?)\s*```', code, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 其次匹配 ``` ... ```
        match = re.search(r'```\s*(.*?)\s*```', code, re.DOTALL)
        if match:
            return match.group(1).strip()

        return code

    def _ast_check(self, code: str) -> Optional[str]:
        """
        AST 预检：在执行前检查代码是否包含危险模式。

        检查项:
            - 语法合法性
            - 禁止 from xxx import * 形式（可能绕过白名单）
            - 禁止 __import__ 直接调用
            - 禁止 exec/eval 嵌套调用
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"语法错误: {e}"

        for node in ast.walk(tree):
            # 检查 from xxx import * 形式
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split('.')[0] not in self.allowed_modules:
                    return f"安全限制: 不允许从模块 '{node.module}' 导入"

            # 检查直接调用 __import__
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == '__import__':
                    return "安全限制: 不允许直接调用 __import__()"

            # 检查 exec/eval 调用
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ('exec', 'eval', 'compile'):
                    return f"安全限制: 不允许调用 {node.func.id}()"

            # 检查对 __builtins__ 的访问
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == '__builtins__':
                    return "安全限制: 不允许访问 __builtins__"

        return None

    def _execute_in_subprocess(self, code: str, attempt: int) -> SandboxResult:
        """在子进程中执行代码并返回结果。"""
        # 序列化数据集
        try:
            data_pickle = pickle.dumps(self.datasets)
        except Exception as e:
            return SandboxResult(
                success=False,
                error_msg=f'数据集序列化失败: {e!s}',
                final_code=code,
                attempt=attempt,
            )

        # 创建子进程
        ctx = multiprocessing.get_context('spawn')
        result_queue = ctx.Queue()
        proc = ctx.Process(
            target=_subprocess_worker,
            args=(code, data_pickle, self.allowed_modules, result_queue),
        )
        proc.start()
        proc.join(self.timeout)

        # 双阶段超时处理：先 SIGTERM，等宽限期后再 SIGKILL
        if proc.is_alive():
            proc.terminate()
            proc.join(self.terminate_grace_period)
            if proc.is_alive():
                proc.kill()
                proc.join()
            return SandboxResult(
                success=False,
                error_msg=f'代码执行超时（>{self.timeout}秒）',
                final_code=code,
                attempt=attempt,
            )

        # 获取结果
        if result_queue.empty():
            return SandboxResult(
                success=False,
                error_msg='子进程异常终止，未返回任何结果',
                final_code=code,
                attempt=attempt,
            )

        try:
            raw = result_queue.get_nowait()
            return SandboxResult(
                success=raw.get('success', False),
                stdout=raw.get('stdout', ''),
                stderr=raw.get('stderr', ''),
                return_value=raw.get('return_value'),
                error_msg=raw.get('error_msg'),
                final_code=code,
                attempt=attempt,
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                error_msg=f'无法获取子进程结果: {e!s}',
                final_code=code,
                attempt=attempt,
            )


# ======================================================================
# 使用示例
# ======================================================================

if __name__ == "__main__":
    import pandas as pd

    # 示例 LLM 修复回调（实际项目中替换为真实的大模型调用）
    def llm_fix_code(code: str, error: str, retry: int) -> str:
        """
        LLM 修复回调示例。
        实际使用时，应调用大模型 API（如 OpenAI / DeepSeek 等）进行代码修复。
        """
        prompt = f"""
你是一名数据分析专家。以下 Python 代码运行时出错，请修复代码。

### 当前代码
{code}

### 错误信息
{error}

### 要求
- 只能使用 pandas、numpy、math 等数据分析库。
- 最终结果必须赋值给变量 result。
- 只输出修复后的完整 Python 代码，不要包含任何解释。
""".strip()
        # 实际调用示例:
        # import openai
        # response = openai.ChatCompletion.create(
        #     model="gpt-4",
        #     messages=[{"role": "user", "content": prompt}],
        #     temperature=0,
        # )
        # return response.choices[0].message.content.strip()

        # 此处仅作演示，返回原始代码
        print(f"[LLM Fix] 第 {retry} 次修复尝试...")
        return code

    # 创建测试数据集
    df_sales = pd.DataFrame({
        "product_id": [1, 2, 1, 3, 2],
        "amount": [100, 200, 150, 300, 250],
    })
    df_products = pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["Apple", "Banana", "Cherry"],
    })

    # 创建沙盒执行器
    executor = SandboxExecutor(
        datasets={"df_sales": df_sales, "df_products": df_products},
        llm_fix_func=llm_fix_code,
        max_retries=2,
        timeout=10,
    )

    # 测试1: 正常代码
    print("=" * 60)
    print("测试1: 正常代码执行")
    print("=" * 60)
    result, history = executor.run("result = df_sales['amount'].sum()")
    print(f"成功: {result.success}")
    print(f"结果: {result.return_value}")
    print(f"尝试次数: {history.total_attempts}")

    # 测试2: 带 markdown 围栏的代码
    print("\n" + "=" * 60)
    print("测试2: 带 markdown 围栏的代码")
    print("=" * 60)
    code_with_fence = """```python
result = df_sales['amount'].mean()
```"""
    result, history = executor.run(code_with_fence)
    print(f"成功: {result.success}")
    print(f"结果: {result.return_value}")

    # 测试3: 安全限制 - 尝试导入 os
    print("\n" + "=" * 60)
    print("测试3: 安全限制 - 尝试导入 os")
    print("=" * 60)
    result, history = executor.run("import os\nresult = os.listdir('.')")
    print(f"成功: {result.success}")
    print(f"错误: {result.error_msg}")

    # 测试4: 安全限制 - 尝试调用 exec
    print("\n" + "=" * 60)
    print("测试4: 安全限制 - 尝试调用 exec")
    print("=" * 60)
    result, history = executor.run("exec('import os')")
    print(f"成功: {result.success}")
    print(f"错误: {result.error_msg}")
