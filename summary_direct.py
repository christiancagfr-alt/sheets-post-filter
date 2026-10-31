"""Read an existing Sheet for gallery publication without creating or writing a merge sheet."""
from collections import defaultdict
from urllib.parse import parse_qs, urlsplit

from fetch_posts import authorize_cfg, extract_spreadsheet_id, open_by_url_or_id, index_to_col_letter
from metric_sheet_io import MetricReader
from sheet_links import is_link_column, read_links


def validate_direct_config(cfg):
    if not extract_spreadsheet_id(str(cfg.wm_direct_url or '').strip()):
        raise ValueError('直接发布请填写有效的 Google 表格链接或 ID')
    try:
        header, start = int(cfg.wm_direct_header_row), int(cfg.wm_direct_start_row)
    except (ValueError, TypeError):
        raise ValueError('直接发布的表头行和数据起始行必须是正整数') from None
    if header < 1 or start <= header:
        raise ValueError('直接发布的数据起始行必须在表头行之后')
    return header, start


def open_direct_sheet(cfg, *, log=print, cancelled=lambda:False):
    header, start = validate_direct_config(cfg)
    if cancelled():raise RuntimeError('任务已取消')
    book = open_by_url_or_id(authorize_cfg(cfg,log=log),cfg.wm_direct_url,log=log)
    tabs = [ws for ws in book.worksheets() if not (getattr(ws,'_properties',{}) or {}).get('hidden')]
    title = str(cfg.wm_direct_sheet or '').strip()
    parsed = urlsplit(str(cfg.wm_direct_url))
    gid = (parse_qs(parsed.fragment).get('gid') or parse_qs(parsed.query).get('gid') or [''])[0]
    if title:
        selected = next((ws for ws in tabs if ws.title==title),None)
    elif gid:
        selected = next((ws for ws in tabs if str(ws.id)==gid),None)
    elif len(tabs)==1:
        selected = tabs[0]
    else:
        raise ValueError('此表包含多个可见 Sheet，请填写直接发布 Sheet 名称，或粘贴带 gid 的工作表链接')
    if selected is None:
        raise ValueError('找不到指定的可见 Sheet；隐藏工作表不会读取')
    if header > selected.row_count:
        raise ValueError('直接发布的表头行超出工作表行数')
    return book, selected, header, start


def read_direct_headers(cfg, *, log=print):
    from worksheet_merge import _a1
    book, ws, header, _start = open_direct_sheet(cfg,log=log)
    values = MetricReader(book,log=log).read(_a1(ws.title,0,header,ws.col_count-1,header),render='FORMATTED_VALUE',what='读取直接发布表头')
    columns = [{'header':str(value).strip(),'column':index_to_col_letter(i),'hyperlink':is_link_column({'header':str(value)})}
               for i,value in enumerate(values[0] if values else []) if str(value).strip()]
    if not columns:raise ValueError('指定行没有表头，请检查直接发布的表头行')
    return columns


def read_direct_rows(cfg, *, log=print, cancelled=lambda:False):
    from summary_publish import media_settings
    from worksheet_merge import _a1
    book, ws, _header, start = open_direct_sheet(cfg,log=log,cancelled=cancelled)
    cfg._wm_direct_width = ws.col_count
    _kind, _days, _zone, columns = media_settings(cfg)
    right = max(index for index in columns.values() if index is not None)
    link_columns = [columns['link']]
    if columns['thumbnail'] is not None:link_columns.append(columns['thumbnail'])
    reader = MetricReader(book,log=log,cancelled=cancelled)
    rows, links = [], []
    for top in range(start,ws.row_count+1,2000):
        reader.check_cancelled()
        bottom = min(top+1999,ws.row_count)
        values = reader.read(_a1(ws.title,0,top,right,bottom),render='FORMATTED_VALUE',what=f'直接读取「{ws.title}」{top}-{bottom} 行')
        found = read_links(book,ws.title,top,bottom,link_columns,values,0,reader)
        by_row = defaultdict(dict)
        for (offset,column),url in found.items():by_row[offset][column]=url
        for offset,row in enumerate(values):
            if any(str(value).strip() for value in row):
                rows.append(row);links.append(by_row.get(offset,{}))
    log(f'直接发布：从「{ws.title}」读取 {len(rows)} 行，不创建或写入汇总表')
    return rows, links
