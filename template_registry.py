"""Desktop templates and lossless migration of removed menu configurations."""
from copy import deepcopy

TEMPLATES = (
    ("filter", "贴文筛选汇总", "按日期、点赞筛选贴文库"),
    ("posts", "贴文汇总", "按数据列表链接读取订阅表，对照贴文库后写入整合表"),
    ("video", "视频提取时长", "读视频链接，写日志表和数据表"),
    ("worksheet_merge", "多工作表汇总", "图片、KB视频、效果视频；可汇总后发布，也可直接读取表格发布"),
)
TEMPLATE_NAMES = {key: title for key, title, _description in TEMPLATES}


def new_template_settings(template):
    """Create an independent configuration, never copy another menu or account."""
    if template not in TEMPLATE_NAMES:
        raise ValueError("未知模板")
    from dataclasses import asdict
    from fetch_posts import Config
    settings = asdict(Config())
    for key in ("ui_menus", "ui_archived_menus", "ui_active_menu"):
        settings.pop(key, None)
    # Structural column/row defaults are safe; user data and account selection
    # must always be entered explicitly on a new configuration.
    settings.update(credentials_file="", credentials_files=[], cf_profile_id="",
                    vd_types=[], vd_report_categories=[], vd_exclude_types=[])
    return settings


def partition_menus(menus, archived=None):
    """Keep unsupported menus recoverable, but never display or schedule them."""
    active, inactive = [], deepcopy(archived or [])
    for item in menus or []:
        if not isinstance(item, dict):
            continue
        (active if item.get("template") in TEMPLATE_NAMES else inactive).append(deepcopy(item))
    return active, inactive
