"""
sandbox_executor.py
安全、可重试的代码沙盒执行器，适用于大模型生成的 pandas 数据分析代码。
"""

import multiprocessing
import pickle
import sys
import io
import traceback
import logging
from typing import Any, Callable, Dict, List, Optional

import openai

logger = logging.getLogger(__name__)


class SandboxExecutor:
    """
    在子进程中安全执行数据分析代码，并提供自动修复重试机制。

    使用示例:
        def llm_fix(code: str, error: str, retry: int) -> str:
            # 调用大模型根据 error 修复 code
            return fixed_code

        executor = SandboxExecutor(
            datasets={"sales": df_sales, "products": df_products},
            llm_fix_func=llm_fix,
            max_retries=3,
            timeout=30
        )
        result = executor.run("sales_summary = sales.groupby('product_id').sum()")
        if result['success']:
            print(result['result'])
        else:
            print(result['error'])
    """

    # 数据分析常用安全模块白名单（可按需扩充）
    DEFAULT_ALLOWED_MODULES = [
        'pandas', 'numpy', 'math', 'statistics',
        'scipy', 'sklearn', 'matplotlib', 'seaborn',
        'datetime', 'collections', 'itertools', 'functools',
        're', 'json', 'typing', 'operator'
    ]

    def __init__(
        self,
        datasets: Dict[str, Any],
        llm_fix_func: Callable[[str, str, int], str],
        max_retries: int = 3,
        timeout: int = 30,
        allowed_modules: Optional[List[str]] = None,
    ):
        """
        参数:
            datasets: 变量名 → pandas DataFrame 的字典，代码中可直接使用这些变量。
            llm_fix_func: 修复函数，签名为 (当前代码, 错误信息, 重试次数) -> 修复后的代码。
            max_retries: 最大自动修复重试次数（首次执行失败后开始重试）。
            timeout: 单次代码执行的最大时长（秒）。
            allowed_modules: 允许导入的顶级模块名列表，默认使用数据科学常用库。
        """
        self.datasets = datasets
        self.llm_fix_func = llm_fix_func
        self.max_retries = max_retries
        self.timeout = timeout
        self.allowed_modules = allowed_modules or self.DEFAULT_ALLOWED_MODULES

        # 确保子进程使用 spawn 方式启动，跨平台行为一致
        try:
            multiprocessing.set_start_method('spawn', force=False)
        except RuntimeError:
            pass  # 已经设置过

    def run(self, code: str) -> Dict[str, Any]:
        """
        执行代码，失败时自动修复重试。

        返回字典:
            success: bool
            result: 执行成功时的结果（变量 'result' 的值，或标准输出）
            error: 失败时的错误信息
            retries: 实际重试次数
            final_code: 最后一次执行的代码
            stdout: 捕获的标准输出
        """
        current_code = code
        last_error = None

        for retry in range(self.max_retries + 1):  # 首次执行 + max_retries 次重试
            exec_result = self._execute_in_subprocess(current_code)

            if exec_result['success']:
                return {
                    'success': True,
                    'result': exec_result['result'],
                    'stdout': exec_result.get('stdout', ''),
                    'retries': retry,
                    'final_code': current_code,
                }

            # 执行失败，记录错误
            last_error = exec_result['error']
            logger.warning("代码执行失败 (尝试 %d/%d): %s", retry, self.max_retries, last_error)

            # 如果没有重试机会了，退出
            if retry >= self.max_retries:
                break

            # 调用大模型修复代码
            try:
                current_code = self.llm_fix_func(current_code, last_error, retry)
                logger.info("大模型已返回修复代码，长度: %d", len(current_code))
            except Exception as fix_err:
                logger.error("调用大模型修复函数失败: %s", fix_err)
                break

        # 所有尝试均失败
        return {
            'success': False,
            'error': last_error,
            'retries': self.max_retries,
            'final_code': current_code,
            'stdout': exec_result.get('stdout', ''),
        }

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _execute_in_subprocess(self, code: str) -> Dict[str, Any]:
        """在子进程中执行代码并返回结果。"""
        # 序列化数据集
        try:
            data_pickle = pickle.dumps(self.datasets)
        except Exception as e:
            return {'success': False, 'error': f'数据集序列化失败: {e!s}'}

        # 结果队列
        ctx = multiprocessing.get_context('spawn')
        result_queue = ctx.Queue()
        proc = ctx.Process(
            target=_subprocess_worker,
            args=(code, data_pickle, self.allowed_modules, result_queue),
        )
        proc.start()
        proc.join(self.timeout)

        if proc.is_alive():
            proc.kill()
            proc.join()
            return {'success': False, 'error': f'代码执行超时（>{self.timeout}秒）'}

        # 获取结果
        try:
            result = result_queue.get_nowait()
            return result
        except Exception as e:
            return {'success': False, 'error': f'无法获取子进程结果: {e!s}'}


def _subprocess_worker(
    code: str,
    data_pickle: bytes,
    allowed_modules: List[str],
    result_queue: multiprocessing.Queue,
) -> None:
    """
    子进程入口：重建环境并执行用户代码。
    该函数必须位于模块顶层，以便 pickle 序列化。
    """
    # 恢复数据集
    try:
        datasets = pickle.loads(data_pickle)
    except Exception:
        result_queue.put({'success': False, 'error': f'数据集反序列化失败:\n{traceback.format_exc()}'})
        return

    # ------------------- 构建受控内置函数 -------------------
    safe_builtins = {
        'True': True,
        'False': False,
        'None': None,
        'abs': abs,
        'all': all,
        'any': any,
        'bin': bin,
        'bool': bool,
        'chr': chr,
        'dict': dict,
        'divmod': divmod,
        'enumerate': enumerate,
        'filter': filter,
        'float': float,
        'format': format,
        'frozenset': frozenset,
        'getattr': getattr,
        'hasattr': hasattr,
        'hash': hash,
        'hex': hex,
        'int': int,
        'isinstance': isinstance,
        'issubclass': issubclass,
        'iter': iter,
        'len': len,
        'list': list,
        'map': map,
        'max': max,
        'min': min,
        'next': next,
        'oct': oct,
        'ord': ord,
        'pow': pow,
        'print': print,
        'range': range,
        'repr': repr,
        'reversed': reversed,
        'round': round,
        'set': set,
        'slice': slice,
        'sorted': sorted,
        'str': str,
        'sum': sum,
        'tuple': tuple,
        'type': type,
        'zip': zip,
        # 常用异常
        'Exception': Exception,
        'ValueError': ValueError,
        'KeyError': KeyError,
        'TypeError': TypeError,
        'IndexError': IndexError,
        'StopIteration': StopIteration,
        'ArithmeticError': ArithmeticError,
        'FileNotFoundError': FileNotFoundError,
    }

    # 白名单导入函数
    import builtins

    original_import = builtins.__import__

    def safe_import(name, *args, **kwargs):
        top_module = name.split('.')[0]
        if top_module not in allowed_modules:
            raise ImportError(f"不允许导入模块 '{name}'")
        return original_import(name, *args, **kwargs)

    safe_builtins['__import__'] = safe_import

    # 构建执行用的全局命名空间
    exec_globals = {
        '__builtins__': safe_builtins,
        '__name__': '__sandbox__',
    }
    exec_globals.update(datasets)  # 用户数据作为变量注入

    # 设置 matplotlib 非交互式后端，避免显示错误
    try:
        import matplotlib
        matplotlib.use('Agg')
    except Exception:
        pass

    # 重定向标准输出/错误
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    sys.stdout = stdout_buf
    sys.stderr = stderr_buf

    success = True
    result = None
    error = None

    try:
        exec(code, exec_globals)
        # 优先获取代码中赋值的 'result' 变量
        if 'result' in exec_globals:
            result = exec_globals['result']
        else:
            output = stdout_buf.getvalue()
            result = output.strip() if output.strip() else None
    except Exception:
        success = False
        # 提取简明的错误信息（限制长度，避免超出大模型 token 限制）
        error = ''.join(traceback.format_exception_only(*sys.exc_info()[:2]))[:3000]
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    # 尝试序列化结果，不可序列化时转为字符串
    serializable_result = None
    if success and result is not None:
        try:
            pickle.dumps(result)
            serializable_result = result
        except Exception:
            serializable_result = str(result)

    result_queue.put({
        'success': success,
        'result': serializable_result,
        'stdout': stdout_buf.getvalue(),
        'error': error,
    })


from sandbox_executor import SandboxExecutor
import pandas as pd


def llm_fix_code(code: str, error: str, retry: int) -> str:
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
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    # 使用
    df1 = pd.DataFrame({"a": [1,2,3], "b": [4,5,6]})
    df2 = pd.DataFrame({"c": [7,8,9]})

    executor = SandboxExecutor(
        datasets={"sales": df1, "extra": df2},
        llm_fix_func=llm_fix_code,
        max_retries=2,
        timeout=10
    )

    result = executor.run("result = sales['a'].sum()")
    print(result)