"""本机管理员引导命令：python manage_users.py create-admin --username ... --name ... --dept dept1。"""
import argparse
from getpass import getpass

from core import accounts, db


def main():
    parser = argparse.ArgumentParser(description='在本机交互创建管理员；密码不进入命令行或日志。')
    parser.add_argument('action', choices=['create-admin'])
    parser.add_argument('--username', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--dept', required=True)
    args = parser.parse_args()
    password = getpass('管理员密码（8–128 位）：')
    if password != getpass('再次输入密码：'):
        parser.error('两次输入的密码不一致。')
    try:
        user = accounts.create_user(args.username, args.name, args.dept, password, role='admin')
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        conn = getattr(db._local, 'conn', None)
        if conn:
            conn.close()
            db._local.conn = None
    print('管理员已创建：%s（%s）' % (user['display_name'], user['username']))


if __name__ == '__main__':
    main()
