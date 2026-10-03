# -*- coding: utf-8 -*-
"""
阶段 36 测试：合规声明贴合「AI 生成图水印去除」场景。

覆盖三类断言：
  1. 控制台完整声明（config.COMPLIANCE_NOTICE）口径正确；
  2. UI 顶部精简声明（i18n app.compliance）中英双语口径正确；
  3. 全部源码 / 文档中的合规块已无旧泛化措辞（"原创图片 / 拥有完整著作权"），
     且统一改为聚焦 AI 图片水印的措辞（回归防护）。
"""

import os
import sys

import config
import utils.i18n as i18n

# 测试文件位于 tests/，项目根目录为其上一级。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 旧泛化措辞（阶段 36 之前），任何文件都不得再出现。
LEGACY_PHRASE = "本人拥有完整著作权的原创图片"
# 新措辞关键字（聚焦 AI 生成图水印）。
NEW_PHRASE = "AI 图片"

# 需要扫描一致性的人工维护文件清单。
SOURCE_FILES = [
    "config.py",
    "main.py",
    "ui/__init__.py",
    "utils/__init__.py",
    "utils/exceptions.py",
    "core/__init__.py",
    "services/__init__.py",
    "core/detector.py",
    "core/mask_generator.py",
    "requirements.txt",
    "docs/01-架构设计文档.md",
    "docs/10-多输入源设计.md",
]


def test_compliance_notice_focuses_on_ai_watermark():
    """控制台完整声明应聚焦 AI 生成图水印，保留底线与不鼓励第三方署名提示。"""
    n = config.COMPLIANCE_NOTICE
    # 场景化：聚焦本人生成的 AI 图片上的可见水印 / 署名标识。
    assert NEW_PHRASE in n, "控制台声明未提及 AI 图片场景"
    assert "本人生成的" in n, "控制台声明未明确「本人生成」归属"
    # 保留底线：明确禁止清除他人著作权作品水印 / 署名。
    assert "明确禁止" in n and "他人享有著作权" in n, "控制台声明缺失底线条款"
    # 保留法律依据。
    assert "第五十三条" in n, "控制台声明缺失《著作权法》第五十三条依据"
    # 新增：不鼓励去除 AI 平台第三方署名水印（通常违反服务条款）。
    assert "不建议" in n and "第三方署名水印" in n, "控制台声明缺失第三方署名水印不鼓励提示"
    # 不得残留旧泛化措辞。
    assert "原创图片" not in n, "控制台声明残留旧「原创图片」措辞"
    assert LEGACY_PHRASE not in n, "控制台声明残留旧「拥有完整著作权」措辞"


def test_i18n_compliance_zh():
    """UI 顶部精简声明（中文）应聚焦 AI 图片水印场景，无旧措辞。"""
    i18n.set_language("zh-CN")
    c = i18n.t("app.compliance")
    assert "本人生成的 AI 图片" in c, "中文 UI 合规声明未聚焦 AI 图片场景"
    assert "第三方署名水印" in c, "中文 UI 合规声明缺失第三方署名水印提示"
    assert "原创图片" not in c, "中文 UI 合规声明残留旧「原创图片」措辞"


def test_i18n_compliance_en():
    """UI 顶部精简声明（英文）应聚焦 AI 图片水印场景。"""
    i18n.set_language("en-US")
    c = i18n.t("app.compliance")
    assert "AI images you generated" in c, "英文 UI 合规声明未聚焦 AI 图片场景"
    assert "Terms of Service" in c, "英文 UI 合规声明缺失服务条款提示"


def test_no_legacy_compliance_phrase_in_source():
    """全部源码 / 文档合规块已统一为新口径（回归防护，防止旧措辞回流）。"""
    missing = []
    legacy = []
    for rel in SOURCE_FILES:
        path = os.path.join(PROJECT_ROOT, rel)
        text = open(path, encoding="utf-8").read()
        if LEGACY_PHRASE in text:
            legacy.append(rel)
        if NEW_PHRASE not in text and "AI生成" not in text:
            missing.append(rel)
    assert not legacy, f"以下文件仍含旧措辞：{legacy}"
    assert not missing, f"以下文件缺少新措辞：{missing}"
