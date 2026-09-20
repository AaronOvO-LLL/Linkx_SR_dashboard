#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""字段模型体检

用法（在 workbench 目录下）：
    python check_config.py                  # 校验全部产品
    python check_config.py safety_butler    # 只校验指定产品
    python check_config.py --strict         # 有 warn 也算失败（用于发布前）

改完 config/ 下的 JSON 就跑一次，能立刻发现 key 拼错、枚举越界、
options 漏配之类的问题，不用等跑到页面上才暴露。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core import capabilities, config, fieldmodel, paths  # noqa: E402


def main(argv):
    strict = '--strict' in argv
    targets = [a for a in argv if not a.startswith('-')]
    if not targets:
        targets = [p['product_type'] for p in config.list_products()]
    if not targets:
        print('未在 config/products/ 下找到任何产品。')
        return 1

    print('字段包目录：%s' % paths.FIELD_PACKS_DIR)
    print('可用字段包：%s' % '、'.join(fieldmodel.list_packs()))
    print('-' * 68)

    failed = 0
    for t in targets:
        report, errs = fieldmodel.render_report(t)
        print(report)
        capability_issues = capabilities.validate_capabilities(t)
        for issue in capability_issues:
            print('  ❌ %s' % issue)
        errs += len(capability_issues)
        print()
        if errs or (strict and '⚠️' in report):
            failed += 1

    print('-' * 68)
    if failed:
        print('❌ %d/%d 个产品未通过校验' % (failed, len(targets)))
        return 1
    print('✅ 全部 %d 个产品通过校验' % len(targets))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
