"""部署运行时设置：环境变量覆盖 + 生产/开发模式开关。

设计目标：同一份代码既能在家里 Windows 上双击启动（development），
也能在腾讯云服务器上以生产模式运行（production），差异全部由环境变量决定，
真实密钥只从环境变量读取，绝不硬编码、绝不打印其值。

支持的环境变量：
    LS_ENV             development（默认） / production
    LS_HOST            覆盖监听地址（默认取 config/app.json，开发 127.0.0.1，生产 0.0.0.0）
    LS_PORT            覆盖监听端口（默认取 config/app.json）
    LS_SESSION_SECRET  会话签名密钥；生产模式必填，且不得为演示占位值
"""

from . import runtime_env

_PLACEHOLDER_SECRET = 'lingshi-demo-secret-change-me'


def mode():
    """运行模式：production / development。未设置时按 development 处理。"""
    value = (runtime_env.read_env('LS_ENV') or 'development').strip().lower()
    return 'production' if value == 'production' else 'development'


def is_production():
    return mode() == 'production'


def resolve_host(app_cfg):
    """监听地址：环境变量优先；生产默认 0.0.0.0，开发沿用配置（127.0.0.1）。"""
    override = runtime_env.read_env('LS_HOST').strip()
    if override:
        return override
    if is_production():
        return '0.0.0.0'
    return app_cfg['server']['host']


def resolve_port(app_cfg):
    override = runtime_env.read_env('LS_PORT').strip()
    if override:
        return int(override)
    return int(app_cfg['server']['port'])


def resolve_secret_key(app_cfg):
    """会话密钥：环境变量优先。

    生产模式下，缺失或仍为演示占位值时直接抛错拒绝启动——绝不退回可用的默认值，
    否则密钥一旦泄露即可伪造任意分部会话。开发模式允许回退到配置中的演示值，
    以保证本地双击即可运行。
    """
    secret = runtime_env.read_env('LS_SESSION_SECRET').strip()
    if secret and secret != _PLACEHOLDER_SECRET:
        return secret
    if is_production():
        raise RuntimeError(
            '生产模式必须通过环境变量 LS_SESSION_SECRET 设置一个强随机会话密钥，'
            '且不等于演示占位值。可用命令生成：python -c "import secrets;print(secrets.token_urlsafe(48))"'
        )
    return app_cfg['session']['secret_key']


def cookie_secure():
    """是否给会话 Cookie 打 Secure 标记，由独立开关 LS_COOKIE_SECURE 决定，默认关闭。

    为什么不直接绑定生产模式：阶段一服务器走的是 HTTP（IP:8770，备案前无法上 HTTPS），
    若此时开启 Secure，浏览器在明文连接下不会保存/回传 Cookie，登录后会立即掉线。
    因此只有当 HTTPS 真正生效（阶段二）时，才在环境文件里设 LS_COOKIE_SECURE=true。
    """
    return (runtime_env.read_env('LS_COOKIE_SECURE') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def should_open_browser(app_cfg):
    """服务器为无头环境，生产模式一律不自动打开浏览器。"""
    if is_production():
        return False
    return bool(app_cfg['server'].get('open_browser'))


def warn_insecure_departments(dept_names):
    """生产模式下，对仍使用初始口令的分部打印告警（不阻止启动，改密在应用内进行）。

    口令已哈希存储（见 core/departments.py），无法再与默认值做字面比对，
    因此只认 changed 标记：从未在应用内改过密码即视为仍是初始口令。
    """
    if not is_production() or not dept_names:
        return
    print('!' * 56)
    print('  安全警告：以下分部仍在使用初始口令，上线前请逐一登录应用修改：')
    print('    ' + '、'.join(dept_names))
    print('!' * 56)
