"""
Python Analyst Tool - 安全、可靠、具备自愈能力的智能代码执行沙箱
================================================================
核心改进：
1. AST 动态重写：自动捕获最后一行表达式作为 result（支持 Jupyter 习惯）
2. 柔性列名自愈：发生 KeyError 时，自动进行模糊列名匹配并内部重试
3. 增强的数据多模态兼容解包：全面清洗来自本体引擎的原始 JSON 字符串、多表嵌套结果
"""

import json
import io
import sys
import ast
import re
import math
from typing import Any, Dict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import pandas as pd
import numpy as np


def _to_jsonable(value: Any) -> Any:
    """Convert pandas/numpy objects produced by analysis code into JSON-safe values."""
    if isinstance(value, pd.DataFrame):
        return _to_jsonable(_df_to_preview(value))
    if isinstance(value, pd.Series):
        return {
            "name": value.name,
            "index": [str(item) for item in value.head(50).index.tolist()],
            "values": [_to_jsonable(item) for item in value.head(50).tolist()],
            "total": int(len(value)),
        }
    if isinstance(value, np.generic):
        return _to_jsonable(value.item())
    if isinstance(value, np.ndarray):
        return [_to_jsonable(item) for item in value.tolist()]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


class SafeCodeExecutor:
    """安全的 Python 代码执行器，支持 AST 变换与错误自愈"""

    ALLOWED_MODULES = {
        'pandas': 'pd',
        'numpy': 'np',
        'json': 'json',
        'math': 'math',
        'statistics': 'statistics',
        're': 're',
        'datetime': 'datetime',
        'collections': 'collections',
    }

    def __init__(self, timeout_seconds: int = 30, max_output_length: int = 50000):
        self.timeout_seconds = timeout_seconds
        self.max_output_length = max_output_length

    def _transform_last_expr_to_assign(self, code: str) -> Any:
        """使用 AST 将代码最后一行如果是表达式，自动赋值给 'result' 变量"""
        try:
            tree = ast.parse(code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_node = tree.body[-1]
                target = ast.Name(id='result', ctx=ast.Store())
                new_node = ast.Assign(targets=[target], value=last_node.value)
                tree.body[-1] = new_node
                ast.fix_missing_locations(tree)
                return compile(tree, filename="<sandbox>", mode="exec")
        except Exception:
            pass
        return None

    def execute(self, code: str, global_vars: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Python 代码，内置 KeyError 智能容错与重试机制"""
        compiled_code = self._transform_last_expr_to_assign(code)
        exec_target = compiled_code if compiled_code else code

        exec_env = {
            '__builtins__': self._build_safe_builtins(),
            **{alias: __import__(mod_name) for mod_name, alias in self.ALLOWED_MODULES.items()},
            **global_vars,
        }

        run_result = self._execute_in_thread(exec_target, exec_env)

        if not run_result['success'] and run_result.get('error_type') == 'KeyError':
            missing_key = run_result.get('raw_error_msg', '')
            if self._attempt_column_healing(global_vars, missing_key):
                print(f"[python_analyst] Detected KeyError for '{missing_key}'. Applied column healing and retrying...")
                exec_env.update(global_vars)
                retry_result = self._execute_in_thread(exec_target, exec_env)
                if retry_result['success']:
                    return retry_result

        return run_result

    def _execute_in_thread(self, target_code, exec_env: dict) -> dict:
        result_container = {'error': None, 'result_vars': {}}
        output_buffer = io.StringIO()

        def worker():
            old_stdout = sys.stdout
            sys.stdout = output_buffer
            try:
                if isinstance(target_code, str):
                    wrapped = f"{target_code}\nif 'df_result' not in dir() and 'df' in dir(): df_result = df"
                    exec(wrapped, exec_env)
                else:
                    exec(target_code, exec_env)
                
                for var_name in ['result', 'df_result', 'output_data', 'chart_data', 'summary']:
                    if var_name in exec_env:
                        result_container['result_vars'][var_name] = exec_env[var_name]
            except Exception as e:
                result_container['error'] = e
            finally:
                sys.stdout = old_stdout

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker)
            try:
                future.result(timeout=self.timeout_seconds)
            except FuturesTimeoutError:
                return {
                    'success': False,
                    'error_type': 'timeout',
                    'message': f'代码执行超时（{self.timeout_seconds}秒）',
                }

        if result_container['error']:
            err = result_container['error']
            return {
                'success': False,
                'error_type': type(err).__name__,
                'message': str(err),
                'raw_error_msg': str(err).strip("'\""),
                'exception': err
            }

        output = output_buffer.getvalue()
        if len(output) > self.max_output_length:
            output = output[:self.max_output_length] + '\n...（输出已截断）'

        return {
            'success': True,
            'stdout': output,
            'result_vars': result_container['result_vars'],
        }

    def _attempt_column_healing(self, global_vars: dict, missing_key: str) -> bool:
        if not missing_key:
            return False
        
        healed = False
        def normalize(name: str) -> str:
            name = name.lower()
            name = re.sub(r'(sum|count|avg|max|min)\((.*?)\)', r'\2', name)
            return re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', name)

        norm_missing = normalize(missing_key)
        if not norm_missing:
            return False

        for var_name, obj in global_vars.items():
            if isinstance(obj, pd.DataFrame) and not obj.empty:
                for col in obj.columns:
                    if normalize(str(col)) == norm_missing:
                        obj[missing_key] = obj[col]
                        healed = True
        return healed

    def _build_safe_builtins(self) -> Dict[str, Any]:
        return {
            'int': int, 'float': float, 'str': str, 'bool': bool,
            'list': list, 'tuple': tuple, 'dict': dict, 'set': set,
            'abs': abs, 'round': round, 'pow': pow, 'divmod': divmod,
            'sum': sum, 'min': min, 'max': max, 'len': len,
            'range': range, 'enumerate': enumerate, 'zip': zip,
            'map': map, 'filter': filter, 'reversed': reversed,
            'sorted': sorted, 'iter': iter, 'next': next,
            'isinstance': isinstance, 'issubclass': issubclass,
            'hasattr': hasattr, 'getattr': getattr, 'setattr': setattr,
            'callable': callable, 'type': type, 'repr': repr, 'format': format,
            'dir': dir,
            'any': any, 'all': all, 'chr': chr, 'ord': ord,
            'True': True, 'False': False, 'None': None,
            'Exception': Exception, 'ValueError': ValueError,
            'TypeError': TypeError, 'KeyError': KeyError,
            'IndexError': IndexError, 'AttributeError': AttributeError,
            'ZeroDivisionError': ZeroDivisionError,
            'ImportError': ImportError, 'NameError': NameError,
            '__import__': self._safe_import,
            'print': print,
        }

    def _safe_import(self, name: str, *args, **kwargs):
        base_name = name.split('.')[0]
        if base_name in self.ALLOWED_MODULES:
            return __import__(name, *args, **kwargs)
        raise ImportError(f"不允许导入模块 '{name}'。")


def _create_dataframe(data: Any) -> pd.DataFrame:
    """多模态数据摄入清洗管道：将任意历史响应格式安全映射为 DataFrame"""
    if isinstance(data, pd.DataFrame): 
        return data
        
    # 如果数据是 JSON 字符串，先尝试进行结构反序列化
    if isinstance(data, str):
        data = data.strip()
        if (data.startswith('[') and data.endswith(']')) or (data.startswith('{') and data.endswith('}')):
            try:
                data = json.loads(data)
            except Exception:
                pass

    if isinstance(data, list):
        if not data: 
            return pd.DataFrame()
        return pd.DataFrame(data)

    if isinstance(data, dict):
        # 针对 ChatBI 核心数据结构的拆包策略
        if "rows" in data and isinstance(data["rows"], list):
            return pd.DataFrame(data["rows"])
        if "data" in data and isinstance(data["data"], list):
            return pd.DataFrame(data["data"])
        
        # 处理纯粹的单行 KV 字典
        return pd.DataFrame([data]) if not any(isinstance(v, list) for v in data.values()) else pd.DataFrame(data)

    return pd.DataFrame([{"value": data}])


def python_analyze(code: str, data_json: str = "", all_query_data: str = "") -> dict:
    """执行 Python 代码进行数据分析（带增强的反馈上下文）"""
    if not code or not code.strip():
        return {"tool_name": "python_analyze", "result": None, "context": "错误：代码为空", "error": True}

    executor = SafeCodeExecutor()
    global_vars = {}
    query_info = {}

    if all_query_data:
        try:
            all_queries = json.loads(all_query_data)
            if isinstance(all_queries, dict): 
                all_queries = [all_queries]
            
            for i, q in enumerate(all_queries, 1):
                df_name = f"df_{i}"
                result_data = q.get("result", []) if isinstance(q, dict) else q
                query_desc = q.get("query", f"Query {i}") if isinstance(q, dict) else f"Data {i}"
                
                df = _create_dataframe(result_data)
                global_vars[df_name] = df
                query_info[df_name] = query_desc
            
            if all_queries:
                global_vars["df"] = global_vars[f"df_{len(all_queries)}"]
        except Exception as e:
            return {"tool_name": "python_analyze", "result": None, "context": f"解析输入数据失败: {e}", "error": True}
    elif data_json:
        try:
            global_vars["df"] = _create_dataframe(data_json)
        except Exception:
            global_vars["df"] = pd.DataFrame()
    else:
        global_vars["df"] = pd.DataFrame()

    global_vars["query_info"] = query_info
    data_snapshot = _get_dataframe_summary(global_vars)

    exec_result = executor.execute(code, global_vars)

    if not exec_result.get('success'):
        error_type = exec_result.get('error_type', 'Unknown')
        msg = exec_result.get('message', '')
        
        detailed_context = [
            f"❌ Python 执行失败！",
            f"错误类型: {error_type}",
            f"错误信息: {msg}",
            "\n【当前沙箱中可用的 DataFrame 极其精确的列信息表】:",
            data_snapshot,
            "\n💡 建议:",
            "1. 请仔细核对上述‘可用数据’中的列名大小写及前缀，确保代码里的 [键名] 与之一致。",
            "2. 如果不需要复杂的循环，可以直接书写表达式，沙箱会自动捕获最后一行结果。"
        ]
        return {
            "tool_name": "python_analyze",
            "result": None,
            "context": "\n".join(detailed_context),
            "error": True,
            "error_type": error_type,
        }

    stdout = exec_result.get('stdout', '')
    result_vars = exec_result.get('result_vars', {})
    result_parts = []

    if stdout:
        result_parts.append(f"=== 标准输出 (stdout) ===\n{stdout}")

    for var_name in ['result', 'df_result', 'output_data']:
        if var_name in result_vars:
            val = result_vars[var_name]
            if isinstance(val, pd.DataFrame):
                preview = val.head(10).to_dict('records')
                result_parts.append(f"=== 返回数据预览 ({var_name}: {val.shape[0]}行 × {val.shape[1]}列) ===\n{json.dumps(preview, ensure_ascii=False, default=str)}")
            else:
                result_parts.append(f"=== 返回变量 ({var_name}) ===\n{val}")
            break
    
    if not result_parts:
        if stdout:
            final_result = stdout
        else:
            df = global_vars.get('df', pd.DataFrame())
            final_result = f"代码执行成功。Dataframe 现状: {len(df)} 行。"
    else:
        final_result = "\n\n".join(result_parts)

    return {
        "tool_name": "python_analyze",
        "result": final_result[:50000],
        "context": f"Python 分析成功完成。\n可用数据状态:\n{data_snapshot}",
        "error": False,
        "data_preview": {k: _to_jsonable(v) for k, v in result_vars.items()}
    }


def _get_dataframe_summary(global_vars: Dict[str, Any]) -> str:
    lines = []
    for key in sorted(global_vars.keys()):
        if key.startswith("df"):
            obj = global_vars[key]
            if isinstance(obj, pd.DataFrame):
                if obj.empty:
                    lines.append(f"  - 变量 `{key}`: 空的 DataFrame")
                else:
                    cols_desc = [f"{c} ({obj[c].dtype})" for c in obj.columns]
                    lines.append(f"  - 变量 `{key}` ({len(obj)} 行 × {len(obj.columns)} 列):\n      包含字段: {', '.join(cols_desc)}")
    return "\n".join(lines) if lines else "  无可用数据"


def _df_to_preview(df: pd.DataFrame) -> dict:
    return {
        "shape": list(df.shape),
        "columns": list(df.columns),
        "head": df.head(5).to_dict('records')
    }