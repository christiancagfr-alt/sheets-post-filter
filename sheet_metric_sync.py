# -*- coding: utf-8 -*-
"""名字 + 代码 + 日期三重匹配的多工作表指标同步模板。"""

from __future__ import annotations

import re
import unicodedata
import uuid
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from metric_sheet_io import MetricReader, open_metric_spreadsheet, resolve_formula_cells
from gspread.exceptions import WorksheetNotFound

from fetch_posts import (
    SHEETS_EPOCH,
    authorize_cfg,
    col_letter_to_index,
    extract_spreadsheet_id,
    index_to_col_letter,
    with_retry,
)

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class SourceMetricRecord:
    key: tuple[str, str, str]
    name: str
    code: str
    date_label: str
    values: tuple[Any, ...]
    sheet: str
    row: int
    group_col: int
    year: int | None = None


@dataclass(frozen=True)
class PlannedChange:
    row: int
    col: int
    old: Any
    new: Any
    name: str
    code: str
    date_label: str
    source_sheet: str
    source_row: int


@dataclass
class StreamingSourcePlan:
    worksheet: Any
    header_rows: list[list[Any]]
    group_cols: list[int]
    data_start_index: int
    date_col: int
    last_row: int
    snapshot: Any = None


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _identity(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _parse_date(value: Any) -> tuple[int | None, int, int] | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.year, value.month, value.day
    if isinstance(value, date):
        return value.year, value.month, value.day
    if isinstance(value, (int, float)):
        try:
            parsed = SHEETS_EPOCH + timedelta(days=float(value))
            return parsed.year, parsed.month, parsed.day
        except (OverflowError, TypeError, ValueError):
            return None
    raw = _text(value).replace("年", "-").replace("月", "-").replace("日", "")
    raw = raw.replace("/", "-").replace(".", "-")
    raw = raw.split(" ", 1)[0].strip("-")
    full = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if full:
        year, month, day_n = map(int, full.groups())
    else:
        short = re.fullmatch(r"(\d{1,2})-(\d{1,2})", raw)
        if not short:
            return None
        year = None
        month, day_n = map(int, short.groups())
    try:
        date(year or 2000, month, day_n)
    except ValueError:
        return None
    return year, month, day_n


def normalize_date_key(value: Any, mode: str = "month_day") -> tuple[str, int | None] | None:
    parsed = _parse_date(value)
    if not parsed:
        return None
    year, month, day_n = parsed
    if mode == "full_date":
        if year is None:
            return None
        return f"{year:04d}-{month:02d}-{day_n:02d}", year
    return f"{month:02d}-{day_n:02d}", year


def _cell(rows: list[list[Any]], row: int, col: int) -> Any:
    if row < 0 or col < 0 or row >= len(rows) or col >= len(rows[row]):
        return ""
    return rows[row][col]


def _number_or_text(value: Any) -> Any:
    if isinstance(value, bool) or value in (None, ""):
        return value
    if isinstance(value, (int, float)):
        return value
    text = _text(value).replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def build_source_index(
    sheets: list[tuple[str, list[list[Any]]]],
    *,
    code_row: int,
    name_row: int,
    date_col: int,
    data_start_row: int,
    metric_start_col: int,
    group_width: int,
    metric_count: int,
    date_mode: str,
) -> tuple[dict[tuple[str, str, str], SourceMetricRecord], set[tuple[str, str, str]], dict[str, int]]:
    records: dict[tuple[str, str, str], SourceMetricRecord] = {}
    conflicts: set[tuple[str, str, str]] = set()
    years_by_key: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    stats = {"source_rows": 0, "source_records": 0, "blank_identity": 0, "invalid_date": 0}
    for sheet_name, rows in sheets:
        max_cols = max((len(row) for row in rows), default=0)
        # A date is shared by every horizontal person group. Parse it once per
        # row instead of repeating the relatively expensive normalization for
        # every group.
        date_cache: list[tuple[str, int | None] | None | bool] = []
        for row_index in range(max(0, data_start_row - 1), len(rows)):
            raw_date = _cell(rows, row_index, date_col)
            if raw_date in (None, ""):
                date_cache.append(False)
            else:
                date_cache.append(normalize_date_key(raw_date, date_mode))
        for group_col in range(metric_start_col, max_cols, max(1, group_width)):
            code = _text(_cell(rows, code_row - 1, group_col))
            name = _text(_cell(rows, name_row - 1, group_col))
            if not code and not name:
                continue
            if not code or not name:
                stats["blank_identity"] += 1
                continue
            first_data_index = max(0, data_start_row - 1)
            for cache_index, normalized in enumerate(date_cache):
                row_index = first_data_index + cache_index
                if normalized is False:
                    continue
                stats["source_rows"] += 1
                if not normalized:
                    stats["invalid_date"] += 1
                    continue
                date_key, year = normalized
                values = tuple(_cell(rows, row_index, group_col + offset) for offset in range(metric_count))
                key = (_identity(name), _identity(code), date_key)
                record = SourceMetricRecord(
                    key=key,
                    name=name,
                    code=code,
                    date_label=date_key,
                    values=values,
                    sheet=sheet_name,
                    row=row_index + 1,
                    group_col=group_col,
                    year=year,
                )
                if year is not None:
                    years_by_key[key].add(year)
                if key in records:
                    conflicts.add(key)
                else:
                    records[key] = record
                    stats["source_records"] += 1
    if date_mode != "full_date":
        for key, years in years_by_key.items():
            if len(years) > 1:
                conflicts.add(key)
    for key in conflicts:
        records.pop(key, None)
    stats["conflicts"] = len(conflicts)
    return records, conflicts, stats


def plan_target_changes(
    target_values: list[list[Any]],
    target_formulas: list[list[Any]],
    source_index: dict[tuple[str, str, str], SourceMetricRecord],
    conflicts: set[tuple[str, str, str]],
    *,
    name_col: int,
    code_col: int,
    date_row: int,
    data_start_row: int,
    group_start_col: int,
    group_width: int,
    write_count: int,
    date_mode: str,
    as_text: bool = False,
) -> tuple[list[PlannedChange], dict[str, int]]:
    stats = {
        "target_rows": 0,
        "matched_keys": 0,
        "unmatched": 0,
        "conflict_skips": 0,
        "formula_skips": 0,
        "blank_source_skips": 0,
        "unchanged": 0,
        "invalid_target_dates": 0,
    }
    changes: list[PlannedChange] = []
    header = target_values[date_row - 1] if 0 < date_row <= len(target_values) else []
    date_groups: list[tuple[int, str]] = []
    for col in range(group_start_col, len(header), max(1, group_width)):
        normalized = normalize_date_key(header[col] if col < len(header) else "", date_mode)
        if not normalized:
            if col < len(header) and header[col] not in (None, ""):
                stats["invalid_target_dates"] += 1
            continue
        date_groups.append((col, normalized[0]))
    max_rows = len(target_values)
    for row_index in range(max(0, data_start_row - 1), max_rows):
        name = _text(_cell(target_values, row_index, name_col))
        code = _text(_cell(target_values, row_index, code_col))
        if not name and not code:
            continue
        stats["target_rows"] += 1
        if not name or not code:
            stats["unmatched"] += len(date_groups)
            continue
        name_key = _identity(name)
        code_key = _identity(code)
        for group_col, date_key in date_groups:
            key = (name_key, code_key, date_key)
            if key in conflicts:
                stats["conflict_skips"] += 1
                continue
            source = source_index.get(key)
            if source is None:
                stats["unmatched"] += 1
                continue
            stats["matched_keys"] += 1
            for offset in range(min(write_count, len(source.values))):
                new_value = source.values[offset]
                if new_value in (None, ""):
                    stats["blank_source_skips"] += 1
                    continue
                if as_text:
                    new_value = str(new_value)
                col = group_col + offset
                formula = _cell(target_formulas, row_index, col)
                if isinstance(formula, str) and formula.startswith("="):
                    stats["formula_skips"] += 1
                    continue
                old_value = _cell(target_values, row_index, col)
                equal = old_value == new_value if as_text else _number_or_text(old_value) == _number_or_text(new_value)
                if equal:
                    stats["unchanged"] += 1
                    continue
                changes.append(
                    PlannedChange(
                        row=row_index + 1,
                        col=col,
                        old=old_value,
                        new=new_value,
                        name=name,
                        code=code,
                        date_label=date_key,
                        source_sheet=source.sheet,
                        source_row=source.row,
                    )
                )
    stats["changes"] = len(changes)
    return changes, stats


def _split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\n,，;；]+", str(value or ""))
    return [_text(item) for item in raw if _text(item)]


def _worksheet_hidden(ws) -> bool:
    """Return whether Google Sheets marks the worksheet as hidden."""
    properties = getattr(ws, "_properties", None)
    if isinstance(properties, dict) and bool(properties.get("hidden")):
        return True
    for attribute in ("isSheetHidden", "hidden"):
        value = getattr(ws, attribute, False)
        if not callable(value) and bool(value):
            return True
    return False


def _selected_source_sheets(source_ss, cfg, target_sid: str, log: LogFn):
    worksheets = with_retry(lambda: source_ss.worksheets(), log=log, what="列出来源工作表")
    mode = str(getattr(cfg, "sm_source_mode", "all") or "all")
    single = _text(getattr(cfg, "sm_source_sheet", ""))
    exact = {_identity(item) for item in _split_list(getattr(cfg, "sm_exclude_exact", []))}
    contains = [_identity(item) for item in _split_list(getattr(cfg, "sm_exclude_contains", []))]
    source_sid = extract_spreadsheet_id(str(getattr(cfg, "sm_source_url", "") or "")) or ""
    target_sheet = _identity(getattr(cfg, "sm_target_sheet", "")) if source_sid == target_sid else ""
    selected = []
    hidden_titles = []
    for ws in worksheets:
        title_key = _identity(ws.title)
        if _worksheet_hidden(ws):
            hidden_titles.append(ws.title)
            continue
        system = title_key.startswith("_") or title_key in {"运行日志", "任务日志", "系统缓存", "缓存"}
        excluded = title_key in exact or any(token and token in title_key for token in contains)
        if system or excluded or (target_sheet and title_key == target_sheet):
            continue
        if mode == "single" and title_key != _identity(single):
            continue
        selected.append(ws)
    if hidden_titles:
        log(f"已自动跳过 {len(hidden_titles)} 个隐藏工作表：{'、'.join(hidden_titles)}")
    if mode == "single" and not selected:
        if any(_identity(title) == _identity(single) for title in hidden_titles):
            raise RuntimeError(f"指定工作表「{single}」已隐藏，按当前规则不会读取")
        raise RuntimeError(f"来源表中找不到指定工作表「{single}」")
    if not selected:
        raise RuntimeError("排除后没有可扫描的来源工作表")
    return selected


def _worksheet_a1_range(title: str, a1: str = "") -> str:
    quoted = "'" + str(title).replace("'", "''") + "'"
    return f"{quoted}!{a1}" if a1 else quoted


def _read_range(spreadsheet, a1_range, *, render, log, what):
    reader = getattr(spreadsheet, "_metric_reader", None)
    if reader is not None:
        return reader.read(a1_range, render=render, what=what)
    response = spreadsheet.values_get(
        a1_range, params={"majorDimension": "ROWS", "valueRenderOption": render}
    )
    return (response or {}).get("values") or []


def _batch_values_get(spreadsheet, ranges: list[str], *, render: str, log: LogFn, what: str):
    output = []
    for number, a1_range in enumerate(ranges, 1):
        label = what if len(ranges) == 1 else f"{what} {number}/{len(ranges)}"
        output.append(_read_range(spreadsheet, a1_range, render=render, log=log, what=label))
    return output


def _resolve_required(spreadsheet, title, rows, coordinates, log):
    resolve_formula_cells(
        spreadsheet, title, rows, coordinates,
        read=lambda a1, **kwargs: _read_range(spreadsheet, a1, log=log, **kwargs),
        log=log,
    )


def _place_rows(target: list[list[Any]], block: list[list[Any]], start_row: int, start_col: int) -> None:
    """Place a trimmed API range back at its original zero-based sheet coordinates."""
    required_rows = start_row + len(block)
    if len(target) < required_rows:
        target.extend([] for _ in range(required_rows - len(target)))
    for offset, values in enumerate(block):
        row = target[start_row + offset]
        required_cols = start_col + len(values)
        if len(row) < required_cols:
            row.extend("" for _ in range(required_cols - len(row)))
        row[start_col:required_cols] = values


def _prepare_one_source_sheet(
    source_ss,
    worksheet,
    *,
    log: LogFn,
    code_row: int,
    name_row: int,
    date_col: int,
    data_start_row: int,
    metric_start_col: int,
    group_width: int,
    header_chunk_cols: int = 240,
) -> StreamingSourcePlan:
    """Discover one sheet's configured groups without requesting an unbounded row range."""
    reader = getattr(source_ss, "_metric_reader", None)
    if (reader is not None and getattr(source_ss.client, "session", None) is not None
            and worksheet.row_count * worksheet.col_count <= 100_000):
        snapshot = reader.snapshot(worksheet, include_formulas=False)
        rows = snapshot.values
        group_cols = [col for col in range(metric_start_col, worksheet.col_count, max(1, group_width))
                      if _cell(rows, code_row - 1, col) not in (None, "")
                      or _cell(rows, name_row - 1, col) not in (None, "")]
        return StreamingSourcePlan(
            worksheet=worksheet, header_rows=rows, group_cols=group_cols,
            data_start_index=data_start_row - 1, date_col=date_col,
            last_row=worksheet.row_count, snapshot=snapshot,
        )
    rows: list[list[Any]] = []
    header_start = min(code_row, name_row)
    header_end = max(code_row, name_row)
    grid_cols = max(metric_start_col + 1, int(getattr(worksheet, "col_count", 0) or 0))
    width = max(group_width, int(header_chunk_cols or 240))
    start_col = metric_start_col
    while start_col < grid_cols:
        end_col = min(grid_cols - 1, start_col + width - 1)
        a1 = (
            f"{index_to_col_letter(start_col)}{header_start}:"
            f"{index_to_col_letter(end_col)}{header_end}"
        )
        log(
            f"「{worksheet.title}」读取表头 "
            f"{index_to_col_letter(start_col)}:{index_to_col_letter(end_col)}…"
        )
        block = _batch_values_get(
            source_ss,
            [_worksheet_a1_range(worksheet.title, a1)],
            render="FORMULA",
            log=log,
            what=f"读取「{worksheet.title}」表头",
        )[0]
        _place_rows(rows, block, header_start - 1, start_col)
        start_col = end_col + 1
    _resolve_required(
        source_ss, worksheet.title, rows,
        [(row - 1, col) for row in (code_row, name_row)
         for col in range(metric_start_col, grid_cols, max(1, group_width))], log,
    )
    max_cols = max((len(row) for row in rows), default=0)
    group_cols = [
        col
        for col in range(metric_start_col, max_cols, max(1, group_width))
        if _cell(rows, code_row - 1, col) not in (None, "")
        or _cell(rows, name_row - 1, col) not in (None, "")
    ]
    return StreamingSourcePlan(
        worksheet=worksheet,
        header_rows=rows,
        group_cols=group_cols,
        data_start_index=data_start_row - 1,
        date_col=date_col,
        last_row=max(data_start_row, int(getattr(worksheet, "row_count", 0) or data_start_row)),
    )


def _read_streaming_source_sheet(
    source_ss,
    plan: StreamingSourcePlan,
    *,
    log: LogFn,
    metric_count: int,
) -> list[list[Any]]:
    """Read existing date values for matching and displayed metric text for copying."""
    rows = [list(row) for row in plan.header_rows]
    if not plan.group_cols:
        return rows
    if plan.snapshot is not None:
        for row_index in range(plan.data_start_index, len(rows)):
            for group_col in plan.group_cols:
                for offset in range(metric_count):
                    col = group_col + offset
                    if len(rows[row_index]) <= col:
                        rows[row_index].extend([""] * (col + 1 - len(rows[row_index])))
                    rows[row_index][col] = _cell(plan.snapshot.display, row_index, col)
        log(f"「{plan.worksheet.title}」已取得日期和指标显示文本，无需重复请求")
        return rows
    date_letter = index_to_col_letter(plan.date_col)
    ranges: list[tuple[str, int]] = [
        (
            _worksheet_a1_range(
                plan.worksheet.title,
                f"{date_letter}{plan.data_start_index + 1}:{date_letter}{plan.last_row}",
            ),
            plan.date_col,
        )
    ]
    first_group = plan.group_cols[0]
    last_metric_col = plan.group_cols[-1] + metric_count - 1
    ranges.append(
        (
            _worksheet_a1_range(
                plan.worksheet.title,
                f"{index_to_col_letter(first_group)}{plan.data_start_index + 1}:"
                f"{index_to_col_letter(last_metric_col)}{plan.last_row}",
            ),
            first_group,
        )
    )
    for index, (a1_range, start_col) in enumerate(ranges):
        # Dates keep their serial-number representation so locale-dependent
        # display formats cannot change name/code/date matching semantics.
        render = "UNFORMATTED_VALUE" if index == 0 else "FORMATTED_VALUE"
        label = "日期现有值" if index == 0 else "指标显示文本"
        log(f"「{plan.worksheet.title}」读取{label}…")
        block = _read_range(
            source_ss, a1_range, render=render, log=log,
            what=f"读取「{plan.worksheet.title}」{label}",
        )
        _place_rows(rows, block, plan.data_start_index, start_col)
    return rows


def _read_target_sheet_ranges(
    target_ss,
    target_ws,
    *,
    log: LogFn,
    name_col: int,
    code_col: int,
    date_row: int,
    data_start_row: int,
    group_start_col: int,
    group_width: int,
    write_count: int,
    date_mode: str,
):
    """Read only target header, identity columns and writable date groups."""
    title = target_ws.title
    header_rows: list[list[Any]] = []
    grid_cols = max(group_start_col + 1, int(getattr(target_ws, "col_count", 0) or 0))
    start_col = group_start_col
    while start_col < grid_cols:
        end_col = min(grid_cols - 1, start_col + 239)
        a1 = (
            f"{index_to_col_letter(start_col)}{date_row}:"
            f"{index_to_col_letter(end_col)}{date_row}"
        )
        log(f"正在读取目标日期表头 {index_to_col_letter(start_col)}:{index_to_col_letter(end_col)}…")
        block = _batch_values_get(
            target_ss,
            [_worksheet_a1_range(title, a1)],
            render="FORMULA",
            log=log,
            what="读取目标日期表头",
        )[0]
        _place_rows(header_rows, block, date_row - 1, start_col)
        start_col = end_col + 1
    _resolve_required(
        target_ss, title, header_rows,
        [(date_row - 1, col) for col in range(group_start_col, grid_cols, max(1, group_width))], log,
    )
    header = header_rows[date_row - 1] if len(header_rows) >= date_row else []
    group_cols = []
    for col in range(group_start_col, len(header), max(1, group_width)):
        if normalize_date_key(header[col] if col < len(header) else "", date_mode):
            group_cols.append(col)
    right_col = max(name_col, code_col, *(col + write_count - 1 for col in group_cols))
    left_col = min(name_col, code_col, group_cols[0] if group_cols else group_start_col)
    data_a1 = (
        f"{index_to_col_letter(left_col)}{data_start_row}:"
        f"{index_to_col_letter(right_col)}{max(data_start_row, int(getattr(target_ws, 'row_count', 0) or data_start_row))}"
    )
    target_range = _worksheet_a1_range(title, data_a1)
    log(
        f"正在读取目标有效区域 {index_to_col_letter(left_col)}{data_start_row}:"
        f"{index_to_col_letter(right_col)}（不再下载整张表）…"
    )
    data_block = _batch_values_get(
        target_ss,
        [target_range],
        render="FORMULA",
        log=log,
        what="读取目标有效数据",
    )[0]
    values: list[list[Any]] = []
    formulas: list[list[Any]] = []
    _place_rows(values, [header], date_row - 1, 0)
    _place_rows(values, data_block, data_start_row - 1, left_col)
    _place_rows(formulas, data_block, data_start_row - 1, left_col)
    _resolve_required(
        target_ss, title, values,
        [(r, col) for r in range(data_start_row - 1, len(values)) for col in (name_col, code_col)], log,
    )
    return values, formulas


def _change_batches(changes: list[PlannedChange], batch_size: int):
    # 同一行连续列合并为一个范围，减少 API 更新项。
    ranges: list[dict[str, Any]] = []
    ordered = sorted(changes, key=lambda item: (item.row, item.col))
    index = 0
    while index < len(ordered):
        first = ordered[index]
        values = [first.new]
        end_col = first.col
        index += 1
        while index < len(ordered) and ordered[index].row == first.row and ordered[index].col == end_col + 1:
            values.append(ordered[index].new)
            end_col = ordered[index].col
            index += 1
        ranges.append(
            {
                "range": f"{index_to_col_letter(first.col)}{first.row}:{index_to_col_letter(end_col)}{first.row}",
                "values": [values],
            }
        )
    for offset in range(0, len(ranges), max(1, batch_size)):
        yield ranges[offset : offset + max(1, batch_size)]


def _apply_changes_to_values(values: list[list[Any]], changes: list[PlannedChange]) -> None:
    for change in changes:
        _place_rows(values, [[change.new]], change.row - 1, change.col)


def _write_change_set(
    target_ws,
    changes: list[PlannedChange],
    *,
    batch_size: int,
    log: LogFn,
    cancelled,
    label: str,
    on_attempt=None,
    on_commit=None,
    input_option="USER_ENTERED",
) -> tuple[int, bool]:
    written = 0
    ordered = sorted(changes, key=lambda item: (item.row, item.col))
    batches = list(_change_batches(changes, batch_size))
    for number, batch in enumerate(batches, 1):
        if cancelled():
            return written, True
        batch_cells = sum(len(item.get("values", [[]])[0]) for item in batch)
        batch_changes = ordered[written : written + batch_cells]
        if on_attempt:
            on_attempt(batch_changes)
        reader = getattr(target_ws, "_metric_reader", None)
        what = f"写入「{label}」第 {number}/{len(batches)} 批"
        if reader is not None:
            reader.write(batch, input_option=input_option, what=what)
        else:
            with_retry(
                lambda b=batch: target_ws.batch_update(b, value_input_option=input_option),
                log=log, what=what,
            )
        written += batch_cells
        if on_commit:
            on_commit(batch_changes)
    return written, False


def _run_sheet_metric_sync(cfg, *, preview: bool = False, log: LogFn = print, cancelled=None, transaction=None) -> dict[str, Any]:
    cancelled = cancelled or (lambda: False)
    source_url = _text(getattr(cfg, "sm_source_url", ""))
    target_url = _text(getattr(cfg, "sm_target_url", ""))
    target_sheet = _text(getattr(cfg, "sm_target_sheet", ""))
    if not source_url or not target_url or not target_sheet:
        raise RuntimeError("请填写来源表格链接、目标表格链接和目标工作表名称")
    metric_count = max(1, int(getattr(cfg, "sm_source_metric_count", 4) or 4))
    write_count = max(1, int(getattr(cfg, "sm_target_write_count", 4) or 4))
    source_group_width = max(1, int(getattr(cfg, "sm_source_group_width", 6) or 6))
    target_group_width = max(1, int(getattr(cfg, "sm_target_group_width", 8) or 8))
    if write_count > metric_count:
        raise RuntimeError("目标可写指标数不能大于来源读取指标数")
    if metric_count > source_group_width:
        raise RuntimeError("来源读取指标数不能大于来源每组列数")
    if write_count > target_group_width:
        raise RuntimeError("目标可写列数不能大于目标每组列数")
    date_mode = str(getattr(cfg, "sm_date_mode", "month_day") or "month_day")
    source_code_row = max(1, int(getattr(cfg, "sm_source_code_row", 1) or 1))
    source_name_row = max(1, int(getattr(cfg, "sm_source_name_row", 2) or 2))
    source_date_col = col_letter_to_index(str(getattr(cfg, "sm_source_date_col", "A") or "A"))
    source_data_start_row = max(1, int(getattr(cfg, "sm_source_data_start_row", 5) or 5))
    source_metric_start_col = col_letter_to_index(
        str(getattr(cfg, "sm_source_metric_start_col", "F") or "F")
    )
    target_name_col = col_letter_to_index(str(getattr(cfg, "sm_target_name_col", "A") or "A"))
    target_code_col = col_letter_to_index(str(getattr(cfg, "sm_target_code_col", "B") or "B"))
    target_date_row = max(1, int(getattr(cfg, "sm_target_date_row", 3) or 3))
    target_data_start_row = max(1, int(getattr(cfg, "sm_target_data_start_row", 7) or 7))
    target_group_start_col = col_letter_to_index(
        str(getattr(cfg, "sm_target_group_start_col", "L") or "L")
    )
    update_batch_size = max(1, min(500, int(getattr(cfg, "sm_batch_size", 100) or 100)))
    task_id = uuid.uuid4().hex[:12]
    log(f"任务 {task_id} · 阶段 1/5：验证配置和表格权限")
    log("读取引擎：单表合并数值/显示文本，超时自动切换备用接口")
    started = time.perf_counter()
    gc = authorize_cfg(cfg, log=log)
    source_ss = open_metric_spreadsheet(gc, source_url, log=log, cancelled=cancelled)
    if extract_spreadsheet_id(source_url) == extract_spreadsheet_id(target_url):
        target_ss = source_ss
    else:
        target_ss = open_metric_spreadsheet(gc, target_url, log=log, cancelled=cancelled)
    target_sid = extract_spreadsheet_id(target_url) or ""
    try:
        target_ws = target_ss.worksheet(target_sheet)
    except WorksheetNotFound as exc:
        raise RuntimeError(f"目标表格中找不到工作表「{target_sheet}」") from exc
    source_reader = getattr(source_ss, "_metric_reader", None) or MetricReader(source_ss, log=log, cancelled=cancelled)
    target_reader = getattr(target_ss, "_metric_reader", None) or MetricReader(target_ss, log=log, cancelled=cancelled)
    source_ss._metric_reader = source_reader
    target_ss._metric_reader = target_reader
    target_ws._metric_reader = target_reader
    if transaction is not None:
        transaction["worksheet"] = target_ws

    def remember_attempt(changes):
        if transaction is not None:
            for item in changes:
                transaction["originals"].setdefault((item.row, item.col), item)
    source_worksheets = _selected_source_sheets(source_ss, cfg, target_sid, log)
    log("实际扫描来源工作表：" + "、".join(ws.title for ws in source_worksheets))
    log("阶段 2/5：只读取目标匹配和可写区域")
    target_values, target_formulas = _read_target_sheet_ranges(
        target_ss,
        target_ws,
        log=log,
        name_col=target_name_col,
        code_col=target_code_col,
        date_row=target_date_row,
        data_start_row=target_data_start_row,
        group_start_col=target_group_start_col,
        group_width=target_group_width,
        write_count=write_count,
        date_mode=date_mode,
    )
    log("阶段 3/5：逐个来源工作表查询、匹配并立即写入")
    seen_records: dict[tuple[str, str, str], SourceMetricRecord] = {}
    conflicts: set[tuple[str, str, str]] = set()
    changes_by_key: dict[tuple[str, str, str], list[PlannedChange]] = {}
    api_written = 0
    cancelled_task = False
    for number, worksheet in enumerate(source_worksheets, 1):
        if cancelled():
            cancelled_task = True
            break
        title = worksheet.title
        log(f"[{number}/{len(source_worksheets)}] 正在查询「{title}」…")
        plan = _prepare_one_source_sheet(
            source_ss,
            worksheet,
            log=log,
            code_row=source_code_row,
            name_row=source_name_row,
            date_col=source_date_col,
            data_start_row=source_data_start_row,
            metric_start_col=source_metric_start_col,
            group_width=source_group_width,
        )
        if not plan.group_cols:
            log(f"[{number}/{len(source_worksheets)}] 「{title}」没有有效名字/代码组，已跳过")
            continue
        rows = _read_streaming_source_sheet(
            source_ss,
            plan,
            log=log,
            metric_count=metric_count,
        )
        local_index, local_conflicts, _local_stats = build_source_index(
            [(title, rows)],
            code_row=source_code_row,
            name_row=source_name_row,
            date_col=source_date_col,
            data_start_row=source_data_start_row,
            metric_start_col=source_metric_start_col,
            group_width=source_group_width,
            metric_count=metric_count,
            date_mode=date_mode,
        )

        # Preserve the old global duplicate rule while still writing one sheet
        # at a time. If a later sheet reveals a duplicate, restore the original
        # target values written for the earlier occurrence.
        duplicate_keys = set(local_conflicts)
        duplicate_keys.update(key for key in local_index if key in seen_records)
        for key in duplicate_keys:
            conflicts.add(key)
            seen_records.pop(key, None)
            earlier_changes = changes_by_key.pop(key, [])
            if earlier_changes and not preview:
                reversals = [
                    PlannedChange(
                        row=item.row,
                        col=item.col,
                        old=item.new,
                        new=item.old,
                        name=item.name,
                        code=item.code,
                        date_label=item.date_label,
                        source_sheet=item.source_sheet,
                        source_row=item.source_row,
                    )
                    for item in earlier_changes
                ]
                log(f"[{number}/{len(source_worksheets)}] 发现跨表重复，撤回先前写入 {len(reversals)} 格…")
                reverted, stopped = _write_change_set(
                    target_ws,
                    reversals,
                    batch_size=update_batch_size,
                    log=log,
                    cancelled=cancelled,
                    label=f"{title}-重复撤回",
                    on_attempt=remember_attempt,
                    input_option="RAW",
                )
                api_written += reverted
                if stopped:
                    cancelled_task = True
                    break
                _apply_changes_to_values(target_values, reversals)
        if cancelled_task:
            break
        fresh_index = {
            key: record
            for key, record in local_index.items()
            if key not in conflicts and key not in duplicate_keys
        }
        seen_records.update(fresh_index)
        changes, sheet_stats = plan_target_changes(
            target_values,
            target_formulas,
            fresh_index,
            conflicts,
            name_col=target_name_col,
            code_col=target_code_col,
            date_row=target_date_row,
            data_start_row=target_data_start_row,
            group_start_col=target_group_start_col,
            group_width=target_group_width,
            write_count=write_count,
            date_mode=date_mode,
            as_text=True,
        )
        for item in changes:
            key = (_identity(item.name), _identity(item.code), item.date_label)
            changes_by_key.setdefault(key, []).append(item)
        if preview:
            log(f"[{number}/{len(source_worksheets)}] 「{title}」预计修改 {len(changes)} 格（预览不写入）")
            continue
        if not changes:
            log(f"[{number}/{len(source_worksheets)}] 「{title}」无需修改，继续下一表")
            continue
        log(f"[{number}/{len(source_worksheets)}] 「{title}」匹配完成，立即写入 {len(changes)} 格…")
        sheet_written, stopped = _write_change_set(
            target_ws,
            changes,
            batch_size=update_batch_size,
            log=log,
            cancelled=cancelled,
            label=title,
            on_attempt=remember_attempt,
            input_option="RAW",
        )
        api_written += sheet_written
        if stopped:
            cancelled_task = True
            break
        _apply_changes_to_values(target_values, changes)
        log(f"[{number}/{len(source_worksheets)}] 「{title}」写入完成：{sheet_written} 格")

    log("阶段 4/5：核对最终匹配结果")
    final_changes = [item for items in changes_by_key.values() for item in items]
    _coverage_changes, coverage_stats = plan_target_changes(
        target_values,
        target_formulas,
        seen_records,
        conflicts,
        name_col=target_name_col,
        code_col=target_code_col,
        date_row=target_date_row,
        data_start_row=target_data_start_row,
        group_start_col=target_group_start_col,
        group_width=target_group_width,
        write_count=write_count,
        date_mode=date_mode,
        as_text=True,
    )
    sample = [
        {
            "name": item.name,
            "code": item.code,
            "date": item.date_label,
            "source": f"{item.source_sheet}!{item.source_row}",
            "target": f"{index_to_col_letter(item.col)}{item.row}",
            "old": item.old,
            "new": item.new,
        }
        for item in final_changes[:100]
    ]
    planned_total = len(final_changes)
    written_total = 0 if preview else planned_total
    log("阶段 5/5：整理执行结果")
    result = {
        "ok": True,
        "task_id": task_id,
        "mode": "sheet_metric_preview" if preview else "sheet_metric_sync",
        "preview": preview,
        "source_sheets": [ws.title for ws in source_worksheets],
        "source_records": len(seen_records),
        "conflicts": len(conflicts),
        "matched": coverage_stats["matched_keys"],
        "planned_cells": planned_total,
        "written_cells": written_total,
        "formula_skips": coverage_stats["formula_skips"],
        "blank_source_skips": coverage_stats["blank_source_skips"],
        "unmatched": coverage_stats["unmatched"],
        "sample": sample,
        "read_requests": source_reader.calls + target_reader.calls,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    if cancelled_task:
        result["cancelled"] = True
    if preview:
        for item in sample[:20]:
            log(
                f"预览：{item['name']} / {item['code']} / {item['date']} · "
                f"{item['source']} → {item['target']} · {item['old']} → {item['new']}"
            )
        if planned_total > 20:
            log(f"预览明细仅显示前 20 条，另外还有 {planned_total - 20} 个拟修改单元格")
        log("预览完成：未写入任何目标单元格")
        return result
    if cancelled_task:
        result["cancelled"] = True
        log(f"任务已取消：本次已执行 {api_written} 次单元格写入/撤回，未处理的来源表不再继续")
        return result
    log(f"同步完成：确认写入 {written_total} 个单元格，未修改目标后四列及其他业务区域")
    return result


def run_sheet_metric_sync(cfg, *, preview=False, log=print, cancelled=None):
    """Run the per-sheet pipeline and restore attempted writes on failure.

    Until every source has been read, cross-sheet conflicts may still exist.
    Any incomplete run must restore its writes to preserve the global rule.
    """
    transaction = {"worksheet": None, "originals": {}}

    def rollback():
        worksheet = transaction["worksheet"]
        originals = list(transaction["originals"].values())
        if worksheet is None or not originals or preview:
            return
        reversals = [PlannedChange(
            row=item.row, col=item.col, old=item.new, new=item.old,
            name=item.name, code=item.code, date_label=item.date_label,
            source_sheet=item.source_sheet, source_row=item.source_row,
        ) for item in originals]
        log(f"来源未全部核验，正在恢复本次已尝试写入的 {len(reversals)} 格原始值…")
        reader = getattr(worksheet, "_metric_reader", None)
        if reader is not None:
            reader.cancelled = lambda: False
        _write_change_set(
            worksheet, reversals, batch_size=100, log=log,
            cancelled=lambda: False, label="恢复原始值", input_option="RAW",
        )
        log("本次写入已恢复；可以重新运行")

    try:
        result = _run_sheet_metric_sync(
            cfg, preview=preview, log=log, cancelled=cancelled, transaction=transaction,
        )
        if result.get("cancelled"):
            rollback()
            result["written_cells"] = 0
        return result
    except Exception as exc:
        try:
            rollback()
        except Exception as restore_error:
            raise RuntimeError(
                f"同步失败，且原始值恢复未完成。请先检查目标表再重跑。恢复错误：{restore_error}"
            ) from exc
        raise
