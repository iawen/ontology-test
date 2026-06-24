import io
import sys
import traceback
import multiprocessing
import re
import pandas as pd
from typing import Dict, Any, Callable, Optional, Tuple

class SandboxResult:
    """沙盒执行结果对象"""
    def __init__(self, success: bool, stdout: str, stderr: str, return_value: Any, error_msg: Optional[str] = None):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.return_value = return_value  # 默认捕获代码中赋给 `result` 变量的值
        self.error_msg = error_msg

def _sandbox_worker(code: str, df_dict: Dict[str, pd.DataFrame], result_queue: multiprocessing.Queue):
    """子进程执行核心（物理隔离标准输出与崩溃）"""
    # 1. 重定向标准输出捕获
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    
    # 2. 构建隔离的执行上下文，注入数据集和常用库
    # 预先切换 matplotlib 后端，防止 plt.show() 导致进程挂起
    init_code = "import matplotlib\nmatplotlib.use('Agg')\nimport pandas as pd\nimport numpy as np\n"
    full_code = init_code + code
    
    exec_locals = {**df_dict}
    exec_globals = {"__builtins__": __builtins__}
    
    try:
        exec(full_code, exec_globals, exec_locals)
        
        # 提取 LLM 约定俗成的返回变量，比如 'result'
        return_value = exec_locals.get('result', None)
        
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        
        result_queue.put(SandboxResult(
            success=True,
            stdout=stdout_val,
            stderr=stderr_val,
            return_value=return_value
        ))
    except Exception as e:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        error_msg = traceback.format_exc()
        
        result_queue.put(SandboxResult(
            success=False,
            stdout=stdout_val,
            stderr=stderr_val,
            return_value=None,
            error_msg=error_msg
        ))
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

class RobustCodeSandbox:
    def __init__(self, max_retries: int = 3, timeout_seconds: int = 15):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    def _clean_code(self, code: str) -> str:
        """清洗 LLM 输出，剔除 markdown 标记"""
        code = code.strip()
        # 匹配 ```python ... ``` 或 ``` ... ```
        match = re.search(r'```python\s*(.*?)\s*```', code, re.DOTALL)
        if match:
            return match.group(1)
        match_generic = re.search(r'```\s*(.*?)\s*```', code, re.DOTALL)
        if match_generic:
            return match_generic.group(1)
        return code

    def run(self, 
            initial_code: str, 
            dataframes: Dict[str, pd.DataFrame], 
            llm_fix_callback: Callable[[str, str], str]) -> Tuple[SandboxResult, list]:
        """
        执行沙盒，包含自动修复逻辑
        :param initial_code: LLM 生成的初始代码
        :param dataframes: 键值对，形如 {"df_user": df1, "df_order": df2}
        :param llm_fix_callback: 修复函数，接收 (当前代码, 错误信息)，返回修正后的新代码
        """
        current_code = self._clean_code(initial_code)
        execution_history = []
        
        for attempt in range(1, self.max_retries + 1):
            result_queue = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=_sandbox_worker, 
                args=(current_code, dataframes, result_queue)
            )
            
            process.start()
            process.join(timeout=self.timeout_seconds)
            
            # 处理超时情况
            if process.is_alive():
                process.terminate()
                process.join()
                res = SandboxResult(
                    success=False,
                    stdout="",
                    stderr="",
                    return_value=None,
                    error_msg=f"TimeoutError: Execution exceeded {self.timeout_seconds}s limit."
                )
            else:
                if result_queue.empty():
                    res = SandboxResult(
                        success=False,
                        stdout="",
                        stderr="",
                        return_value=None,
                        error_msg="UnknownError: Sandbox process terminated abruptly."
                    )
                else:
                    res = result_queue.get()
            
            execution_history.append({"attempt": attempt, "code": current_code, "result": res})
            
            # 执行成功则直接返回
            if res.success:
                return res, execution_history
            
            # 执行失败且还有重试机会，触发 LLM 自动修复
            if attempt < self.max_retries:
                print(f"[Sandbox] 第 {attempt}次 执行失败，正在调用 LLM 进行修复... Error: {res.error_msg.splitlines()[-1]}")
                try:
                    # 调用外部传入的 LLM 修复函数
                    raw_fixed_code = llm_fix_callback(current_code, res.error_msg)
                    current_code = self._clean_code(raw_fixed_code)
                except Exception as fix_err:
                    res.error_msg += f"\n[Sandbox Fix Error] 调用修复组件失败: {str(fix_err)}"
                    return res, execution_history
            else:
                print(f"[Sandbox] 达到最大重试次数 ({self.max_retries})，执行最终失败。")
                
        return res, execution_history

# ==================== LLM 修复回调 ====================
def llm_fix_agent(failed_code: str, traceback_str: str) -> str:
    """
    这里编写你项目实际对接大模型（如 DeepSeek, GPT-4 等）的代码
    """
    prompt = f"""
    你是一个 Python 代码修复专家。以下代码在执行时报错了。
    请检查代码逻辑并修复它。数据集中包含两个 DataFrame: 'df_sales' 和 'df_users'。
    
    【错误堆栈】:
    {traceback_str}
    
    【原代码】:
    {failed_code}
    
    请直接返回修复后的完整 Python 代码，包裹在 ```python ... ``` 中。最终的分析结果请赋值给变量 `result`。
    """
    
    # 模拟大模型修复：这里我们硬编码一个正确的代码作为 LLM 的返回
    # 现实中应调用 client.chat.completions.create(...)
    print(" -> [LLM Agent] 正在思考如何修复 Bug...")
    
    correct_code = """
    ```python
    # 修正了原代码中错误的列名（将 user_idx 修正为 user_id）
    merged_df = pd.merge(df_sales, df_users, left_on='user_id', right_on='id')
    result = merged_df.groupby('name')['amount'].sum().reset_index()
    print("计算成功完成！")
    ```
    """
    return correct_code