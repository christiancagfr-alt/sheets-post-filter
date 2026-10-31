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
        outer=tk.Frame(self); outer.pack(fill='both',expand=True)
        canvas=tk.Canvas(outer,highlightthickness=0)
        bar=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left',fill='both',expand=True);bar.pack(side='right',fill='y')
        panel=tk.Frame(canvas,padx=18,pady=14)
        window=canvas.create_window((0,0),window=panel,anchor='nw')
        panel.bind('<Configure>',lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        self.alias=tk.StringVar();self.token=tk.StringVar();self.profile=tk.StringVar();self.site=tk.StringVar()
        self.domain=tk.StringVar();self.password=tk.StringVar();self.kind=tk.StringVar(value='多工作表图库')
        self.all_menus=tk.BooleanVar();self.status=tk.StringVar();self.menu_vars={}
        tk.Label(panel,text='1. 账号',font=('',12,'bold')).pack(anchor='w')
        self.combo=ttk.Combobox(panel,textvariable=self.profile,state='readonly')
        self.combo.pack(fill='x',pady=6);self.combo.bind('<<ComboboxSelected>>',lambda _e:self.load_selected())
        tk.Label(panel,text='账号备注名称').pack(anchor='w');ttk.Entry(panel,textvariable=self.alias).pack(fill='x')
        tk.Label(panel,text='添加 / 更新账号的 API Token（隐藏保存；同一 Token 可识别多个账号）').pack(anchor='w',pady=(10,2))
        ttk.Entry(panel,textvariable=self.token,show='•').pack(fill='x')
        self.login_button=ttk.Button(panel,text='验证 Token 并添加账号',command=self.login)
        self.login_button.pack(anchor='w',pady=6)
        tk.Label(panel,text='2. 此账号允许发布哪些配置',font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        ttk.Checkbutton(panel,text='允许所有可发布配置（包含以后新增的配置）',variable=self.all_menus).pack(anchor='w')
        for menu in app._menus:
            if menu['template'] not in ('filter','worksheet_merge'):continue
            var=tk.BooleanVar();self.menu_vars[menu['id']]=var
            ttk.Checkbutton(panel,text=menu['name'],variable=var).pack(anchor='w',padx=12)
        ttk.Button(panel,text='保存账号名称与发布范围',command=self.save_scope).pack(anchor='w',pady=6)
        self.check_button=ttk.Button(panel,text='只读检查所选账号的 Pages / R2',command=self.check_account)
        self.check_button.pack(anchor='w',pady=4)
        tk.Label(panel,text='3. 此账号的站点',font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        self.site_combo=ttk.Combobox(panel,textvariable=self.site,state='readonly')
        self.site_combo.pack(fill='x');self.site_combo.bind('<<ComboboxSelected>>',lambda _e:self.load_site())
        ttk.Button(panel,text='把所选站点绑定到当前左侧配置',command=self.bind_site).pack(anchor='w',pady=6)
        ttk.Label(panel,text='图片和视频使用两个汇总菜单，分别绑定到同一账号、同一多工作表图库域名。',wraplength=720).pack(anchor='w')
        tk.Label(panel,text='4. 部署 / 更新站点',font=('',12,'bold')).pack(anchor='w',pady=(12,4))
        ttk.Combobox(panel,textvariable=self.kind,values=('多工作表图库','贴文图库'),state='readonly').pack(fill='x')
        tk.Label(panel,text='域名（填写自己的域名，不带路径）').pack(anchor='w');ttk.Entry(panel,textvariable=self.domain).pack(fill='x')
        tk.Label(panel,text='网站访问密码（留空公开访问）').pack(anchor='w');ttk.Entry(panel,textvariable=self.password,show='•').pack(fill='x')
        self.deploy_button=ttk.Button(panel,text='部署所选账号的站点',command=self.deploy)
        self.deploy_button.pack(anchor='w',pady=8)
        ttk.Label(panel,textvariable=self.status,wraplength=720).pack(anchor='w',pady=6)
        ttk.Button(panel,text='关闭',command=self.destroy).pack(anchor='e')
        self.refresh()

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
        if site:self.kind.set('多工作表图库' if site.get('kind')=='summary' else '贴文图库')

    def save_scope(self):
        try:
            domain,password,kind=self.domain.get(),self.password.get(),self.kind.get()
            row=self.selected()
            row.update(name=self.alias.get().strip() or row['account_id'],all_menus=self.all_menus.get(),
                       allowed_menu_ids=[key for key,var in self.menu_vars.items() if var.get()])
            save_profile(row);self.refresh(row['id'])
            self.domain.set(domain);self.password.set(password);self.kind.set(kind)
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

    def deploy(self):
        host=normalize_host(self.domain.get())
        kind='summary' if self.kind.get()=='多工作表图库' else 'promo'
        password=self.password.get()
        row=self.save_scope()
        if not row:return
        if not host or '.' not in host:
            messagebox.showerror('缺少域名','请填写自己的站点域名',parent=self);return
        self.deploy_button.configure(state='disabled');self.status.set('开始部署…')
        def work():
            try:
                result=deploy_gallery(host,token=row['api_token'],account_id=row['account_id'],profile_id=row['id'],
                                      site_kind=kind,access_password=password,
                                      log=lambda message:self.after(0,lambda text=message:self.status.set(text)))
                self.after(0,lambda:self.deployed(row['id'],result['host']))
            except Exception as exc:self.after(0,lambda error=str(exc):self.status.set(error))
            finally:self.after(0,lambda:self.deploy_button.configure(state='normal'))
        threading.Thread(target=work,daemon=True).start()

    def deployed(self,identity,host):
        self.refresh(identity);self.site.set(host);self.load_site()
        self.status.set('部署完成。选择左侧汇总配置，再点击“把所选站点绑定到当前左侧配置”。')
