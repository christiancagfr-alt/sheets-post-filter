"""Cloudflare multi-account management UI. Network actions run only on explicit clicks."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from cf_accounts import login_accounts, profiles, profile_by_id, save_profile, site_for_menu
from promo_deploy import CloudflareAPI, deploy_gallery, known_gallery_sites, load_cf_state, normalize_host


class CloudflareAccountsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('Cloudflare 多账号与发布范围')
        self.geometry('820x820')
        self.minsize(740, 650)
        bg, ink = '#f1f5f9', '#1e293b'
        self.configure(bg=bg)
        outer=tk.Frame(self, bg=bg); outer.pack(fill='both',expand=True)
        canvas=tk.Canvas(outer, bg=bg, highlightthickness=0)
        bar=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left',fill='both',expand=True);bar.pack(side='right',fill='y')
        panel=tk.Frame(canvas, bg=bg, padx=18, pady=14)
        window=canvas.create_window((0,0),window=panel,anchor='nw')
        panel.bind('<Configure>',lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        self.alias=tk.StringVar();self.token=tk.StringVar();self.profile=tk.StringVar();self.site=tk.StringVar()
        self.domain=tk.StringVar();self.password=tk.StringVar();self.publish_secret=tk.StringVar();self.kind=tk.StringVar(value='多工作表图库')
        self.all_menus=tk.BooleanVar();self.status=tk.StringVar();self.menu_vars={}
        tk.Label(panel,text='1. 账号',bg=bg,fg=ink,font=('',12,'bold')).pack(anchor='w')
        self.combo=ttk.Combobox(panel,textvariable=self.profile,state='readonly')
        self.combo.pack(fill='x',pady=6);self.combo.bind('<<ComboboxSelected>>',lambda _e:self.load_selected())
        tk.Label(panel,text='账号备注名称',bg=bg,fg=ink).pack(anchor='w');ttk.Entry(panel,textvariable=self.alias).pack(fill='x')
        tk.Label(panel,text='添加 / 更新账号的 API Token（隐藏保存；同一 Token 可识别多个账号）',bg=bg,fg=ink).pack(anchor='w',pady=(10,2))
        ttk.Entry(panel,textvariable=self.token,show='•').pack(fill='x')
        self.login_button=ttk.Button(panel,text='验证 Token 并添加账号',command=self.login)
        self.login_button.pack(anchor='w',pady=6)
        tk.Label(panel,text='2. 此账号允许发布哪些配置',bg=bg,fg=ink,font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        ttk.Checkbutton(panel,text='允许所有可发布配置（包含以后新增的配置）',variable=self.all_menus).pack(anchor='w')
        for menu in app._menus:
            if menu['template'] not in ('filter','worksheet_merge'):continue
            var=tk.BooleanVar();self.menu_vars[menu['id']]=var
            ttk.Checkbutton(panel,text=menu['name'],variable=var).pack(anchor='w',padx=12)
        ttk.Button(panel,text='保存账号名称与发布范围',command=self.save_scope).pack(anchor='w',pady=6)
        self.check_button=ttk.Button(panel,text='只读检查所选账号的 Pages / R2',command=self.check_account)
        self.check_button.pack(anchor='w',pady=4)
        tk.Label(panel,text='3. 此账号的站点',bg=bg,fg=ink,font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        self.site_combo=ttk.Combobox(panel,textvariable=self.site,state='readonly')
        self.site_combo.pack(fill='x');self.site_combo.bind('<<ComboboxSelected>>',lambda _e:self.load_site())
        ttk.Button(panel,text='把所选站点绑定到当前左侧配置',command=self.bind_site).pack(anchor='w',pady=6)
        ttk.Label(panel,text='多人或多台电脑维护同一站点：导入同一份配置，绑定同一个站点，日常直接点“发布图库”。',wraplength=720).pack(anchor='w')
        tk.Label(panel,text='4. 部署 / 更新站点',bg=bg,fg=ink,font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        ttk.Combobox(panel,textvariable=self.kind,values=('多工作表图库','贴文图库'),state='readonly').pack(fill='x')
        tk.Label(panel,text='域名（填写自己的域名，不带路径）',bg=bg,fg=ink).pack(anchor='w');ttk.Entry(panel,textvariable=self.domain).pack(fill='x')
        tk.Label(panel,text='已有站点发布密钥（也可通过“配置迁移”加密导入，连接时不更换线上密钥）',bg=bg,fg=ink).pack(anchor='w')
        ttk.Entry(panel,textvariable=self.publish_secret,show='•').pack(fill='x')
        self.connect_button=ttk.Button(panel,text='连接已有站点（不部署、不生成密钥）',command=self.connect)
        self.connect_button.pack(anchor='w',pady=6)
        self.restore_button=ttk.Button(panel,text='恢复线上发布密钥（使用输入 / 导入的原值）',command=self.restore_secret)
        self.restore_button.pack(anchor='w',pady=4)
        tk.Label(panel,text='网站访问密码（留空公开访问）',bg=bg,fg=ink).pack(anchor='w');ttk.Entry(panel,textvariable=self.password,show='•').pack(fill='x')
        self.password_button=ttk.Button(panel,text='修改 / 重置所选站点访问密码',command=self.reset_password)
        self.password_button.pack(anchor='w',pady=6)
        self.deploy_button=ttk.Button(panel,text='复用已保存站点（不重新部署）',command=self.deploy)
        self.deploy_button.pack(anchor='w',pady=8)
        self.create_button=ttk.Button(panel,text='创建全新站点',command=lambda:self.deploy(create=True))
        self.create_button.pack(anchor='w',pady=4)
        self.update_button=ttk.Button(panel,text='更新原站点网页程序（保留发布密钥）',command=lambda:self.deploy(update=True))
        self.update_button.pack(anchor='w',pady=4)
        ttk.Label(panel,text='访问请使用自定义域名或固定的 项目名.pages.dev；不要使用部署记录中的随机预览链接。',wraplength=720).pack(anchor='w',pady=4)
        ttk.Label(panel,textvariable=self.status,wraplength=720).pack(anchor='w',pady=6)
        ttk.Button(panel,text='关闭',command=self.destroy).pack(anchor='e')
        self.refresh()
        self.protocol('WM_DELETE_WINDOW',lambda:None if getattr(self,'_site_busy',False) else self.destroy())

    def refresh(self, selected=None):
        self.labels={f"{row['name']} · {row['account_id'][-8:]}":row['id'] for row in profiles()}
        self.combo.configure(values=list(self.labels))
        if selected:
            self.profile.set(next((name for name,identity in self.labels.items() if identity==selected),''))
        if self.profile.get() not in self.labels:
            self.profile.set(next(iter(self.labels),''))
        self.load_selected()
        if hasattr(self.app,'_refresh_wm_cloudflare'):self.app._refresh_wm_cloudflare()

    def selected(self):
        return profile_by_id(self.labels.get(self.profile.get(),''))

    def load_selected(self):
        if not self.profile.get():return
        row=self.selected()
        self.alias.set(row['name']);self.all_menus.set(bool(row.get('all_menus')))
        for key,var in self.menu_vars.items():var.set(key in (row.get('allowed_menu_ids') or []))
        sites=[site for site in known_gallery_sites(load_cf_state()) if site.get('account_id')==row['account_id']]
        self.sites={site['host']:site for site in sites}
        self.site_combo.configure(values=list(self.sites))
        current=next((menu for menu in self.app._menus if menu['id']==self.app._active_menu_id),{})
        preferred='summary' if current.get('template')=='worksheet_merge' else 'promo'
        self.kind.set('多工作表图库' if preferred=='summary' else '贴文图库')
        self.site.set(next((site['host'] for site in sites if site.get('kind','promo')==preferred),''));self.load_site()

    def load_site(self):
        site=getattr(self,'sites',{}).get(self.site.get())
        self.domain.set(site['host'] if site else '')
        self.password.set(site.get('access_password','') if site else '')
        self.publish_secret.set(site.get('secret','') if site else '')
        if site:self.kind.set('多工作表图库' if site.get('kind')=='summary' else '贴文图库')

    def save_scope(self):
        try:
            domain,password,kind,secret=self.domain.get(),self.password.get(),self.kind.get(),self.publish_secret.get()
            row=self.selected()
            row.update(name=self.alias.get().strip() or row['account_id'],all_menus=self.all_menus.get(),
                       allowed_menu_ids=[key for key,var in self.menu_vars.items() if var.get()])
            save_profile(row);self.refresh(row['id'])
            self.domain.set(domain);self.password.set(password);self.kind.set(kind);self.publish_secret.set(secret)
            self.status.set('账号及发布范围已保存')
            return row
        except Exception as exc:
            messagebox.showerror('保存失败',str(exc),parent=self)
            return None

    def login(self):
        token=self.token.get().strip()
        if not token:return
        self.login_button.configure(state='disabled');self.status.set('正在验证账号…')
        def work():
            try:
                accounts=login_accounts(token,allowed_menu_ids=[self.app._active_menu_id])
                self.after(0,lambda:self.login_done(accounts[0]['id']))
            except Exception as exc:
                self.after(0,lambda error=str(exc):self.status.set(error))
            finally:self.after(0,lambda:self.login_button.configure(state='normal'))
        threading.Thread(target=work,daemon=True).start()

    def login_done(self,identity):
        self.token.set('');self.refresh(identity);self.status.set('账号已添加，其他账号的 Token、站点和授权范围已保留')

    def check_account(self):
        try:row=self.selected()
        except Exception as exc:
            self.status.set(str(exc));return
        self.check_button.configure(state='disabled');self.status.set('正在只读检查所选账号…')
        def work():
            try:
                CloudflareAPI(row['api_token']).verify_deploy_permissions(row['account_id'])
                self.after(0,lambda:self.status.set('所选账号的 Pages 与 R2 读取均正常；没有创建资源或修改任何权限'))
            except Exception as exc:self.after(0,lambda error=str(exc):self.status.set(error))
            finally:self.after(0,lambda:self.check_button.configure(state='normal'))
        threading.Thread(target=work,daemon=True).start()

    def bind_site(self):
        try:
            menu=next(row for row in self.app._menus if row['id']==self.app._active_menu_id)
            kind='summary' if menu['template']=='worksheet_merge' else 'promo'
            if menu['template'] not in ('filter','worksheet_merge'):raise ValueError('请先在左侧选择贴文筛选汇总或多工作表汇总配置')
            profile,site=site_for_menu(self.selected()['id'],menu['id'],self.site.get(),kind=kind)
            self.app._store_current_menu_settings()
            menu['settings']['cf_profile_id']=profile['id']
            if kind=='summary':
                menu['settings']['wm_gallery_host']=site['host']
                self.app._wm_vars['cf_profile_id'].set(profile['id'])
                self.app._wm_vars['wm_gallery_host'].set(site['host'])
                self.app._refresh_wm_cloudflare()
            else:
                self.app._filter_cf_profile_id=profile['id']
                self.app.var_cf_url.set(site['publish_url']);self.app.var_cf_secret.set(site['secret'])
            self.app._save(quiet=True)
            self.status.set(f"已绑定「{menu['name']}」到 {site['host']}")
        except Exception as exc:messagebox.showerror('绑定失败',str(exc),parent=self)

    def _site_action(self, operation):
        if getattr(self,'_site_busy',False):return
        row=self.save_scope()
        if not row:return
        host=normalize_host(self.domain.get())
        if not host:return
        buttons=(self.connect_button,self.restore_button,self.password_button,self.deploy_button,self.create_button,self.update_button)
        self._site_busy=True
        for button in buttons:button.configure(state='disabled')
        self.status.set('正在处理现有站点…')
        def work():
            try:
                operation(row,host)
                self.after(0,lambda:self.deployed(row['id'],host))
            except Exception as exc:self.after(0,lambda error=str(exc):self.status.set(error))
            finally:
                self.after(0,lambda:self._site_idle(buttons))
        threading.Thread(target=work,daemon=True).start()

    def _site_idle(self,buttons):
        self._site_busy=False
        for button in buttons:button.configure(state='normal')

    def connect(self):
        from gallery_management import connect_existing
        secret=self.publish_secret.get().strip()
        kind='summary' if self.kind.get()=='多工作表图库' else 'promo'
        self._site_action(lambda row,host:connect_existing(CloudflareAPI(row['api_token']),row['account_id'],host,secret,
            kind=kind,profile_id=row['id']))

    def reset_password(self):
        from gallery_management import change_access_password
        password=self.password.get()
        if not messagebox.askyesno('修改访问密码',
            '将修改所填域名的访问密码，并更新原项目使其生效。原素材和发布密钥保留。'+
            ('\n当前密码为空，将改为无需密码公开访问。' if not password else '\n旧登录将失效，访客需重新登录。'),parent=self):return
        self._site_action(lambda row,host:change_access_password(CloudflareAPI(row['api_token']),row['account_id'],host,password,
            log=lambda message:self.after(0,lambda text=message:self.status.set(text))))

    def restore_secret(self):
        from gallery_management import restore_publish_secret
        secret=self.publish_secret.get().strip()
        if not messagebox.askyesno('恢复原发布密钥',
            '将把输入 / 导入的原发布密钥设置回当前域名的原项目，并更新原部署使其生效。\n'
            '请确认已从原维护电脑找回正确密钥；其他电脑必须使用同一密钥。\n'
            '访问密码和素材数据保留，不会生成新密钥。是否继续？',parent=self):return
        self._site_action(lambda row,host:restore_publish_secret(CloudflareAPI(row['api_token']),row['account_id'],host,secret,
            log=lambda message:self.after(0,lambda text=message:self.status.set(text))))

    def deploy(self,create=False,update=False):
        if getattr(self,'_site_busy',False):return
        host=normalize_host(self.domain.get())
        kind='summary' if self.kind.get()=='多工作表图库' else 'promo'
        password=self.password.get()
        if (create or update) and not messagebox.askyesno('站点部署',
            '只在域名尚未部署时创建全新站点。已有站点不会重新生成发布密钥。' if create else
            '更新现有站点的网页程序；沿用原 Pages、R2 和线上发布密钥。日常只发布数据不需要此操作。',parent=self):return
        if not host or '.' not in host:
            messagebox.showerror('缺少域名','请填写自己的站点域名',parent=self);return
        self._site_action(lambda row,host:deploy_gallery(host,token=row['api_token'],account_id=row['account_id'],profile_id=row['id'],
            site_kind=kind,access_password=password,force_update=update,create_if_missing=create,
            log=lambda message:self.after(0,lambda text=message:self.status.set(text))))

    def deployed(self,identity,host):
        self.refresh(identity);self.site.set(host);self.load_site()
        self.status.set('站点已就绪。可绑定到当前左侧配置；日常直接“发布图库”，无需部署。')
