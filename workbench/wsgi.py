#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生产 WSGI 入口。

供 waitress / gunicorn 等生产 WSGI 服务器加载，例如：
    waitress-serve --host=127.0.0.1 --port=8770 wsgi:app

与开发入口 app.py 的区别：
    - 默认按生产模式运行（LS_ENV=production），强制要求真实会话密钥；
    - 在导入时完成数据库初始化（开发入口是在 main() 里做的）。

注意：录音转写（ASR）走进程内后台线程，SQLite 为单机单写，
因此生产部署必须锁定为「单进程多线程」（waitress 默认即是），
切勿用 gunicorn 开多 worker，否则在途转写任务与写并发都会出问题。
"""
import os

# 生产入口默认即生产模式；如需覆盖请在加载前显式设置 LS_ENV。
os.environ.setdefault('LS_ENV', 'production')

from core import db  # noqa: E402
from app import app  # noqa: E402

# 位于 Nginx 反向代理之后：信任 X-Forwarded-Proto / X-Forwarded-For，
# 使 request.scheme、url_for 生成的地址与真实协议一致（HTTPS 上线后尤为重要）。
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1, x_host=1)

db.init_db()

if __name__ == '__main__':
    # 便于本机快速验证：python wsgi.py
    from waitress import serve
    from core import runtime
    from core.config import app_config
    cfg = app_config()
    host = runtime.resolve_host(cfg)
    port = runtime.resolve_port(cfg)
    print('waitress 承载中： http://%s:%d （模式 %s）' % (host, port, runtime.mode()))
    serve(app, host=host, port=port, threads=8)
