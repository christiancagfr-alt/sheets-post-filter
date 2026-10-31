"""Local Cloudflare account profiles and explicit per-menu publishing permissions."""
from copy import deepcopy
from urllib.parse import urlsplit

from promo_deploy import CloudflareAPI, CloudflareError, known_gallery_sites, load_cf_state, normalize_host, save_cf_state


def profiles(state=None):
    state = load_cf_state() if state is None else state
    result = [deepcopy(row) for row in state.get('profiles') or [] if isinstance(row, dict) and row.get('id')]
    legacy_id = str(state.get('account_id') or '')
    if legacy_id and state.get('api_token') and not any(row['id'] == legacy_id for row in result):
        result.append({'id': legacy_id, 'name': state.get('account_name') or legacy_id,
                       'account_id': legacy_id, 'api_token': state['api_token'], 'allowed_menu_ids': [], 'all_menus': False})
    return result


def save_profile(profile):
    state = load_cf_state()
    rows = profiles(state)
    rows = [row for row in rows if row['id'] != profile['id']] + [deepcopy(profile)]
    # Tag legacy sites before selecting a different account; never infer their owner from a later login.
    sites = known_gallery_sites(state)
    for site in sites:
        site.setdefault('account_id', state.get('account_id') or '')
    save_cf_state({'profiles': rows, 'sites': sites})


def register_accounts(token, accounts, *, allowed_menu_ids=None):
    previous = {row['id']: row for row in profiles()}
    for account in accounts:
        row = previous.get(account['id'], {})
        save_profile({**row, 'id': account['id'], 'account_id': account['id'],
                      'name': row.get('name') or account['name'], 'api_token': token,
                      'allowed_menu_ids': row.get('allowed_menu_ids', list(allowed_menu_ids or [])),
                      'all_menus': bool(row.get('all_menus', False))})


def login_accounts(token, *, allowed_menu_ids=None):
    from promo_deploy import looks_like_global_api_key, AUTH_HELP
    if looks_like_global_api_key(token):
        raise CloudflareError('需要 API Token，不能使用 Global API Key。' + AUTH_HELP)
    accounts = CloudflareAPI(token).list_accounts()
    if not accounts:
        raise CloudflareError('令牌无法读取任何 Cloudflare 账号')
    register_accounts(token, accounts, allowed_menu_ids=allowed_menu_ids)
    return accounts


def profile_by_id(profile_id):
    row = next((item for item in profiles() if item['id'] == profile_id), None)
    if not row:
        raise ValueError('请先在 Cloudflare 账号管理中添加并选择账号')
    return row


def require_menu(profile_id, menu_id):
    profile = profile_by_id(profile_id)
    if not menu_id or not (profile.get('all_menus') or menu_id in (profile.get('allowed_menu_ids') or [])):
        raise ValueError('此 Cloudflare 账号未获准发布当前配置，请在账号管理中勾选该汇总配置并保存')
    return profile


def site_for_menu(profile_id, menu_id, host, *, kind=None):
    profile = require_menu(profile_id, menu_id)
    host = normalize_host(host)
    site = next((item for item in known_gallery_sites(load_cf_state()) if item['host'] == host), None)
    if not site or site.get('account_id') != profile['account_id']:
        raise ValueError('该域名不属于所选账号的已记录站点，请先在账号管理中部署或选择正确站点')
    if kind and site.get('kind', 'promo') != kind:
        raise ValueError('该域名的站点类型与当前汇总模板不匹配，请选择多工作表图库站点')
    return profile, site


def validate_legacy_publish(cfg):
    profile_id = str(getattr(cfg, 'cf_profile_id', '') or '')
    if profile_id:
        site_for_menu(profile_id, cfg.ui_active_menu, urlsplit(cfg.cf_publish_url).hostname or '', kind='promo')
