"""Preserve link labels and destinations without copying arbitrary cell formatting."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def http_url(value):
    text = str(value or '').strip()
    try:
        if urlsplit(text).scheme.lower() in ('http', 'https') and urlsplit(text).netloc:
            return text
    except ValueError:
        pass
    return ''


def cell_link(cell):
    direct = http_url(cell.get('hyperlink'))
    if direct:
        return direct
    candidates = [((cell.get('userEnteredFormat') or {}).get('textFormat') or {}).get('link', {}).get('uri')]
    candidates += [((run.get('format') or {}).get('link') or {}).get('uri') for run in cell.get('textFormatRuns') or []]
    candidates += [((run.get('chip') or {}).get('richLinkProperties') or {}).get('uri') for run in cell.get('chipRuns') or []]
    formula = str((cell.get('userEnteredValue') or {}).get('formulaValue') or '')
    match = re.search(r'(?i)HYPERLINK\s*\(\s*"((?:[^"]|"")+)"', formula)
    if match:
        candidates.append(match.group(1).replace('""', '"'))
    return next((url for value in candidates if (url := http_url(value))), '')


def is_link_column(mapping):
    if 'hyperlink' in mapping:
        return bool(mapping['hyperlink'])
    header = str(mapping.get('header') or '').casefold()
    return '链接' in header or 'url' in header


def read_links(spreadsheet, title, top, bottom, columns, rows, left, reader):
    """Return {(relative_row, absolute_column): URL}; fetch rich links only when needed."""
    from fetch_posts import index_to_col_letter
    result = {}
    missing = set()
    for offset, row in enumerate(rows):
        for column in columns:
            index = column - left
            text = row[index] if 0 <= index < len(row) else ''
            url = http_url(text)
            if url:
                result[offset, column] = url
            elif str(text or '').strip():
                missing.add(column)
    if not missing:
        return result
    quoted = "'" + title.replace("'", "''") + "'!"
    params = {
        'ranges': [quoted + index_to_col_letter(c) + str(top) + ':' + index_to_col_letter(c) + str(bottom) for c in sorted(missing)],
        'fields': 'sheets(data(startRow,startColumn,rowData(values(hyperlink,userEnteredValue(formulaValue),userEnteredFormat(textFormat(link)),textFormatRuns(format(link)),chipRuns(chip(richLinkProperties(uri)))))))',
    }

    def request():
        session = getattr(spreadsheet.client, 'session', None)
        if session is None:
            return spreadsheet.fetch_sheet_metadata(params=params)
        response = session.get('https://sheets.googleapis.com/v4/spreadsheets/' + spreadsheet.id,
                               params=params, timeout=(8, reader.timeout))
        return reader._response_json(response)

    data = reader.execute(request, what=f'读取「{title}」单元格超链接')
    for sheet in data.get('sheets', []):
        for block in sheet.get('data', []):
            column = int(block.get('startColumn', 0))
            start = int(block.get('startRow', 0)) - (top - 1)
            if column not in missing:
                continue
            for offset, row in enumerate(block.get('rowData') or []):
                cells = row.get('values') or []
                url = cell_link(cells[0] if cells else {})
                if url and 0 <= start + offset < len(rows):
                    result[start + offset, column] = url
    return result


def write_links(spreadsheet, worksheet, start_row, output, links, columns, reader):
    """Set only link metadata; preserve widths, colors and all other cell formatting."""
    if not columns or not output:
        return
    for start in range(0, len(output), 1000):
        count = min(1000, len(output) - start)
        requests = []
        for relative, absolute in columns:
            rows = []
            for offset in range(start, start + count):
                url = links[offset].get(relative, '')
                text_format = {'link': {'uri': url}} if url else {}
                # Do not also write textFormatRuns=[]: Google applies that as a
                # rich-text link reset and removes the cell-level link we just set.
                # The preceding RAW values write already clears old text runs.
                rows.append({'values': [{'userEnteredFormat': {'textFormat': text_format, 'hyperlinkDisplayType': 'LINKED'}}]})
            requests.append({'updateCells': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': start_row - 1 + start,
                          'endRowIndex': start_row - 1 + start + count, 'startColumnIndex': absolute, 'endColumnIndex': absolute + 1},
                'rows': rows, 'fields': 'userEnteredFormat.textFormat.link,userEnteredFormat.hyperlinkDisplayType',
            }})
        reader.execute(lambda body={'requests': requests}: spreadsheet.batch_update(body), what=f'写入可点击超链接 {start + 1}-{start + count}')
