import math
import re
import requests
import io

math_env = None
global_t = 0
eval_memo = {}


def parametric_eval(string, **vals):
    global math_env # what is "math_env"?
    if string in eval_memo:
        return eval_memo[string]
    if isinstance(string, str):
        if math_env is None:
            math_env = {
                "abs": abs,
                "max": max,
                "min": min,
                "pow": pow,
                "round": round,
                "__builtins__": None,
            }
            math_env.update(
                {key: getattr(math, key) for key in dir(math) if "_" not in key}
            )
        math_env.update(vals)
        math_env["t"] = global_t
        try:
            output = eval(string, math_env)
        except SyntaxError as e:
            raise RuntimeError("Error in parametric value " + string)
        eval_memo[string] = output
        return output
    else:
        return string


def set_t(t):
    global global_t, eval_memo
    global_t = t
    eval_memo = {}


def fetch(url_or_path):
    if str(url_or_path).startswith("http://") or str(url_or_path).startswith(
        "https://"
    ):
        r = requests.get(url_or_path)
        r.raise_for_status()
        fd = io.BytesIO()
        fd.write(r.content)
        fd.seek(0)
        return fd
    return open(url_or_path, "rb")


def parse(string, split, defaults):
    tokens = re.split(split, string, len(defaults) - 1)
    tokens = tokens + defaults[len(tokens) :]
    return tokens
