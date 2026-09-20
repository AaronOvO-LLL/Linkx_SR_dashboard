"""读取运行时配置，但绝不记录或回显密钥值。"""

import os


def read_env(name, default=''):
    """优先读取进程环境；Windows 下兼容已写入用户环境但尚未继承的变量。"""
    value = os.environ.get(name, '')
    if value:
        return value
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value or '')
        except (FileNotFoundError, OSError):
            pass
    return default


def configured(name):
    return bool(read_env(name).strip())
