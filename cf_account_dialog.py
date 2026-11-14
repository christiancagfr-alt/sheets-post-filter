"""Cloudflare multi-account management UI. Network actions run only on explicit clicks."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

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
        tk.Label(panel,text='Cloudflare 网站部署与管理',bg=bg,fg=ink,font=('',15,'bold')).pack(anchor='w')
        tk.Label(panel,text='当前账号',bg=bg,fg=ink).pack(anchor='w',pady=(10,0))
        self.combo=ttk.Combobox(panel,textvariable=self.profile,state='readonly')
        self.combo.pack(fill='x',pady=6);self.combo.bind('<<ComboboxSelected>>',lambda _e:self.load_selected())
        tabs=ttk.Notebook(panel);tabs.pack(fill='both',expand=True,pady=8)
        account=ttk.Frame(tabs,padding=12);create=ttk.Frame(tabs,padding=12);manage=ttk.Frame(tabs,padding=12)
        tabs.add(create,text='一键部署');tabs.add(manage,text='已有网站');tabs.add(account,text='账号与权限')
        self.tabs=tabs;self.manage_tab=manage
        def label(parent,text): ttk.Label(parent,text=text,wraplength=690).pack(anchor='w',pady=(8,3))
        label(account,'首次使用：添加 Cloudflare API Token。之后部署无需重复填写。')
        label(account,'账号备注名称');ttk.Entry(account,textvariable=self.alias).pack(fill='x')
        label(account,'Cloudflare API Token（账号授权凭据，不是网站发布密钥）')
        ttk.Entry(account,textvariable=self.token,show='•').pack(fill='x')
        from promo_deploy import TOKEN_CREATE_URL
        import webbrowser
        ttk.Button(account,text='打开 Cloudflare 创建账号 Token',command=lambda:webbrowser.open(TOKEN_CREATE_URL)).pack(anchor='w',pady=5)
        self.login_button=ttk.Button(account,text='验证并保存账号',command=self.login);self.login_button.pack(anchor='w',pady=5)
        label(account,'允许这个账号发布的配置')
        ttk.Checkbutton(account,text='允许所有可发布配置',variable=self.all_menus).pack(anchor='w')
        for menu in app._menus:
            if menu['template'] not in ('filter','worksheet_merge'):continue
            var=tk.BooleanVar();self.menu_vars[menu['id']]=var
            ttk.Checkbutton(account,text=menu['name'],variable=var).pack(anchor='w',padx=12)
        ttk.Button(account,text='保存账号与发布范围',command=self.save_scope).pack(anchor='w',pady=8)
        self.check_button=ttk.Button(account,text='检查部署权限',command=self.check_account);self.check_button.pack(anchor='w')
        current=next((m for m in app._menus if m['id']==app._active_menu_id),{})
        self.create_menu_id=current.get('id')
        self.new_kind='summary' if current.get('template')=='worksheet_merge' else 'promo'
        type_name='成品库 / 多工作表图库' if self.new_kind=='summary' else '帖文筛选图库'
        self.create_binding_label=tk.StringVar(value='绑定配置：'+str(current.get('name') or '未选择')+' · '+type_name)
        ttk.Label(create,textvariable=self.create_binding_label,wraplength=690).pack(anchor='w',pady=(8,3))
        label(create,'自动创建独立网站和存储，生成发布密钥，完成后自动绑定上面的配置。两类网站使用各自的模板和发布接口。')
        self.new_password=tk.StringVar();self.new_domain=tk.StringVar();self.project_name=tk.StringVar()
        label(create,'网站访问密码（选填；留空允许公开访问）')
        ttk.Entry(create,textvariable=self.new_password,show='•').pack(fill='x')
        self.use_custom_domain=tk.BooleanVar(value=False)
        ttk.Checkbutton(create,text='使用自定义域名（默认免费 pages.dev 地址）',variable=self.use_custom_domain,command=self.toggle_domain).pack(anchor='w',pady=10)
        self.new_domain_entry=ttk.Entry(create,textvariable=self.new_domain,state='disabled');self.new_domain_entry.pack(fill='x')
        label(create,'项目名称（选填；留空自动生成）');ttk.Entry(create,textvariable=self.project_name).pack(fill='x')
        label(create,'节省配额：静态文件直出、数据未变化跳过发布；访问统计不写入素材存储。若 Cloudflare 提示账号尚未开通 R2，需账号所有者先在 Cloudflare 控制台完成 R2 开通，再回来重试。')
        self.create_button=ttk.Button(create,text='一键部署并绑定当前配置',command=lambda:self.deploy(create=True));self.create_button.pack(fill='x',pady=16)
        progress=ttk.LabelFrame(create,text='部署进度',padding=6);progress.pack(fill='both',expand=True,pady=(0,8))
        self.create_log=tk.Text(progress,height=7,wrap='word',state='disabled',background='#f8fafc',foreground='#1e293b')
        log_scroll=ttk.Scrollbar(progress,orient='vertical',command=self.create_log.yview)
        self.create_log.configure(yscrollcommand=log_scroll.set)
        self.create_log.pack(side='left',fill='both',expand=True);log_scroll.pack(side='right',fill='y')
        label(manage,'选择已有网站（只显示当前账号的网站）')
        self.site_combo=ttk.Combobox(manage,textvariable=self.site,state='readonly');self.site_combo.pack(fill='x')
        self.site_combo.bind('<<ComboboxSelected>>',lambda _e:self.load_site())
        self.site_kind_label=ttk.Label(manage);self.site_kind_label.pack(anchor='w',pady=6)
        ttk.Button(manage,text='绑定所选网站到当前配置',command=self.bind_site).pack(anchor='w',pady=4)
        self.deploy_button=ttk.Button(manage,text='检查并复用所选网站',command=self.deploy);self.deploy_button.pack(anchor='w',pady=4)
        self.update_button=ttk.Button(manage,text='更新所选网站程序（保留素材、密码和密钥）',command=lambda:self.deploy(update=True));self.update_button.pack(anchor='w',pady=4)
        ttk.Button(manage,text='查看访问分析',command=self.show_analytics).pack(anchor='w',pady=6)
        advanced=ttk.LabelFrame(manage,text='密码、密钥与连接已有网站',padding=10);advanced.pack(fill='x',pady=10)
        label(advanced,'站点类型');ttk.Combobox(advanced,textvariable=self.kind,values=('多工作表图库','贴文图库'),state='readonly').pack(fill='x')
        label(advanced,'网站地址（连接已有网站时填写）');self.domain_entry=ttk.Entry(advanced,textvariable=self.domain);self.domain_entry.pack(fill='x')
        label(advanced,'发布密钥');self.secret_entry=ttk.Entry(advanced,textvariable=self.publish_secret,show='•');self.secret_entry.pack(fill='x')
        label(advanced,'访问密码');self.password_entry=ttk.Entry(advanced,textvariable=self.password,show='•');self.password_entry.pack(fill='x')
        self.show_secrets=tk.BooleanVar(value=False)
        ttk.Checkbutton(advanced,text='显示密码与密钥',variable=self.show_secrets,command=self.toggle_secret_visibility).pack(anchor='w',pady=5)
        ttk.Button(advanced,text='查看已保存凭据',command=self.view_credentials).pack(anchor='w',pady=3)
        self.generate_button=ttk.Button(advanced,text='生成待应用的新密钥',command=self.generate_secret);self.generate_button.pack(anchor='w',pady=3)
        self.restore_button=ttk.Button(advanced,text='应用新发布密钥',command=self.restore_secret);self.restore_button.pack(anchor='w',pady=3)
        self.password_button=ttk.Button(advanced,text='修改访问密码',command=self.reset_password);self.password_button.pack(anchor='w',pady=3)
        self.connect_button=ttk.Button(advanced,text='连接已有网站（保留线上密钥）',command=self.connect);self.connect_button.pack(anchor='w',pady=3)
        ttk.Label(panel,text='两类网站均禁止搜索引擎收录。日常更新数据请使用主界面的“发布图库”。',wraplength=720).pack(anchor='w',pady=5)
        ttk.Label(panel,textvariable=self.status,wraplength=720).pack(anchor='w',pady=6)
        ttk.Button(panel,text='关闭',command=self.destroy).pack(anchor='e')
        self.refresh()
        self.protocol('WM_DELETE_WINDOW',lambda:None if getattr(self,'_site_busy',False) else self.destroy())

    def show_analytics(self):
        site=self.credential_target()
        if not site:return
        account=self.selected()
        win=tk.Toplevel(self);win.title('访问分析 · '+site['host']);win.geometry('840x620')
        win.transient(self)
        top=ttk.Frame(win,padding=12);top.pack(fill='x')
        ttk.Label(top,text=site['host']+' · '+('成品库 / 多工作表图库' if site.get('kind')=='summary' else '帖文筛选图库')).pack(anchor='w')
        ttk.Label(top,text='UTC 时间 · 访客按每日浏览器去重估计；停留为页面前台时长，切到后台后计入。',wraplength=790).pack(anchor='w',pady=5)
        ttk.Label(top,text='不记录 IP、姓名或素材链接。统计可能受拦截、采样及关页未上报影响；历史访问无法补录。',wraplength=790).pack(anchor='w')
        controls=ttk.Frame(top);controls.pack(fill='x',pady=8)
        period=tk.StringVar(value='7')
        ttk.Combobox(controls,textvariable=period,values=('1','7','30'),state='readonly',width=6).pack(side='left')
        ttk.Label(controls,text='天 · 查询结果缓存 5 分钟，不自动刷新').pack(side='left',padx=8)
        status=tk.StringVar(value='点击查询读取所选网站的统计。需要账号 Token 的 Account Analytics / Read 权限。')
        ttk.Label(win,textvariable=status,wraplength=800).pack(fill='x',padx=12,pady=4)
        tabs=ttk.Notebook(win);tabs.pack(fill='both',expand=True,padx=12,pady=8)
        tables={}
        for key,title in (('days','每日访问'),('hours','访问时间段')):
            frame=ttk.Frame(tabs);tabs.add(frame,text=title)
            tree=ttk.Treeview(frame,columns=('period','visitors','visits','duration'),show='headings')
            for column,label in (('period','UTC 时间'),('visitors','浏览器访客数（估计）'),('visits','前台访问次数（估计）'),('duration','平均前台停留（秒）')):
                tree.heading(column,text=label);tree.column(column,width=180,anchor='center')
            scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
            tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y');tables[key]=tree
        def done(result=None,error=None):
            if not win.winfo_exists():return
            button.configure(state='normal')
            if error:status.set(error);return
            for key,tree in tables.items():
                tree.delete(*tree.get_children())
                for item in result[key]:
                    tree.insert('', 'end', values=(item['period'],item['visitors'],item['visits'],item['average_seconds']))
            status.set(('缓存 · ' if result['cached'] else '已查询 · ')+result['updated_at']+
                (' · 存在采样，独立浏览器数可能低估' if result['sampled'] else '')+
                (' · 暂无记录，请访问网站后切换到后台，稍后再查' if not result['days'] else ''))
        def query():
            days=int(period.get());button.configure(state='disabled');status.set('正在读取统计…')
            def work():
                try:
                    from gallery_analytics import read_analytics
                    result=read_analytics(CloudflareAPI(account['api_token']),account['account_id'],site,days)
                    self.after(0,lambda:done(result))
                except Exception as exc:self.after(0,lambda error=str(exc):done(error=error))
            threading.Thread(target=work,daemon=True).start()
        button=ttk.Button(controls,text='查询访问分析',command=query);button.pack(side='right')

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
        self.new_kind=preferred
        self.create_menu_id=current.get('id')
        self.create_binding_label.set('绑定配置：'+str(current.get('name') or '未选择')+' · '+('成品库 / 多工作表图库' if preferred=='summary' else '帖文筛选图库'))
        self.site.set(next((site['host'] for site in sites if site.get('kind','promo')==preferred),''));self.load_site()

    def toggle_domain(self):
        self.new_domain_entry.configure(state="normal" if self.use_custom_domain.get() else "disabled")

    def load_site(self):
        self.show_secrets.set(False)
        self.toggle_secret_visibility()
        site=getattr(self,'sites',{}).get(self.site.get())
        self.domain.set(site['host'] if site else '')
        self.password.set(site.get('access_password','') if site else '')
        self.publish_secret.set(site.get('secret','') if site else '')
        if site:self.kind.set('多工作表图库' if site.get('kind')=='summary' else '贴文图库')
        self.site_kind_label.configure(text=('类型：成品库 / 多工作表图库' if site.get('kind')=='summary' else '类型：帖文筛选图库') if site else '尚未选择网站')

    def save_scope(self):
        try:
            domain,password,kind,secret=self.domain.get(),self.password.get(),self.kind.get(),self.publish_secret.get()
            custom=self.use_custom_domain.get()
            row=self.selected()
            row.update(name=self.alias.get().strip() or row['account_id'],all_menus=self.all_menus.get(),
                       allowed_menu_ids=[key for key,var in self.menu_vars.items() if var.get()])
            save_profile(row);self.refresh(row['id'])
            self.domain.set(domain);self.password.set(password);self.kind.set(kind);self.publish_secret.set(secret);self.use_custom_domain.set(custom);self.toggle_domain()
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

    def _bind_deployed_site(self, menu_id, host):
        menu=next(item for item in self.app._menus if item['id']==menu_id)
        kind='summary' if menu['template']=='worksheet_merge' else 'promo'
        from cf_accounts import require_menu
        require_menu(self.selected()['id'],menu_id)
        if menu_id==self.app._active_menu_id:
            self.app._store_current_menu_settings()
            menu=next(item for item in self.app._menus if item['id']==menu_id)
        profile,site=site_for_menu(self.selected()['id'],menu_id,host,kind=kind)
        menu.setdefault('settings',{})['cf_profile_id']=profile['id']
        if kind=='summary':
            menu['settings']['wm_gallery_host']=site['host']
        else:
            menu['settings'].update(cf_publish_url=site['publish_url'],cf_publish_secret=site['secret'])
        self.app.cfg.ui_menus=self.app._menus
        if menu_id==self.app._active_menu_id:
            if kind=='summary':
                self.app._wm_vars['cf_profile_id'].set(profile['id'])
                self.app._wm_vars['wm_gallery_host'].set(site['host'])
                self.app._refresh_wm_cloudflare()
            else:
                self.app._filter_cf_profile_id=profile['id']
                self.app.var_cf_url.set(site['publish_url'])
                self.app.var_cf_secret.set(site['secret'])
            self.app._save(quiet=True)
        else:
            from fetch_posts import save_config
            save_config(self.app.cfg)
        self.status.set(f"已自动绑定「{menu['name']}」到 {site['host']}")

    def _deployment_log(self, message):
        text=str(message)
        self.status.set(text)
        if not hasattr(self,'create_log'):return
        self.create_log.configure(state='normal')
        self.create_log.insert('end',text+'\n')
        self.create_log.see('end')
        self.create_log.configure(state='disabled')

    def bind_site(self, menu_id=None):
        try:
            menu=next(row for row in self.app._menus if row['id']==(menu_id or self.app._active_menu_id))
            kind='summary' if menu['template']=='worksheet_merge' else 'promo'
            if menu['template'] not in ('filter','worksheet_merge'):raise ValueError('请先在左侧选择贴文筛选汇总或多工作表汇总配置')
            profile,site=site_for_menu(self.selected()['id'],menu['id'],self.site.get(),kind=kind)
            if menu['id']==self.app._active_menu_id:
                self.app._store_current_menu_settings()
            menu['settings']['cf_profile_id']=profile['id']
            if kind=='summary':
                menu['settings']['wm_gallery_host']=site['host']
                if menu['id']==self.app._active_menu_id:
                    self.app._wm_vars['cf_profile_id'].set(profile['id'])
                    self.app._wm_vars['wm_gallery_host'].set(site['host'])
                    self.app._refresh_wm_cloudflare()
            else:
                menu['settings'].update(cf_publish_url=site['publish_url'],cf_publish_secret=site['secret'])
                if menu['id']==self.app._active_menu_id:
                    self.app._filter_cf_profile_id=profile['id']
                    self.app.var_cf_url.set(site['publish_url']);self.app.var_cf_secret.set(site['secret'])
            if menu['id']==self.app._active_menu_id:
                self.app._save(quiet=True)
            else:
                from app import save_config
                self.app.cfg.ui_menus=self.app._menus
                save_config(self.app.cfg)
            self.status.set(f"已绑定「{menu['name']}」到 {site['host']}")
        except Exception as exc:messagebox.showerror('绑定失败',str(exc),parent=self)

    def _site_action(self, operation, *, target_host=None, allow_empty=False, bind_menu_id=None):
        if getattr(self,'_site_busy',False):return
        row=self.selected()
        if not row:return
        host=normalize_host(self.domain.get()) if target_host is None else target_host
        if not host and not allow_empty:return
        buttons=(self.connect_button,self.restore_button,self.password_button,self.deploy_button,self.create_button,self.update_button,self.generate_button)
        self._site_busy=True
        for button in buttons:button.configure(state='disabled')
        if allow_empty:
            self.create_log.configure(state='normal');self.create_log.delete('1.0','end');self.create_log.configure(state='disabled')
            self._deployment_log('开始部署；每个步骤和失败原因会显示在这里。')
        else:self.status.set('正在部署网站…' if allow_empty else '正在处理所选网站…')
        def work():
            try:
                result=operation(row,host)
                completed_host=result.get('host',host) if isinstance(result,dict) else host
                def complete():
                    try:
                        if bind_menu_id:self._bind_deployed_site(bind_menu_id,completed_host)
                        self.deployed(row['id'],completed_host)
                        if allow_empty:
                            details=(f"网站：{result.get('site_url') or 'https://'+completed_host}\n"
                                f"类型：{'成品库 / 多工作表图库' if result.get('kind')=='summary' else '贴文筛选图库'}\n"
                                f"Pages 项目：{result.get('project') or '未返回'}\nR2 存储桶：{result.get('bucket') or '未返回'}\n"
                                f"发布地址：{result.get('publish_url') or '未返回'}\n\n发布密钥已保存在本机配置并回填到发布设置。")
                            self._deployment_log('部署完成，网站、R2、发布接口和本机绑定均已确认。')
                            messagebox.showinfo('网站部署成功',details,parent=self)
                    except Exception as exc:
                        self._deployment_log('网站已创建，但本机配置绑定失败：'+str(exc))
                        messagebox.showerror('部署后绑定失败','网站可能已经部署成功，但软件没有完成本机配置绑定。\n\n'+str(exc)+
                            '\n\n请在“已有网站”中选择该站点并点击“绑定所选网站到当前配置”。',parent=self)
                self.after(0,complete)
            except Exception as exc:
                error=str(exc)
                def failed():
                    if allow_empty:
                        self._deployment_log('部署失败：'+error)
                        if 'Analytics Engine' in error:
                            self._deployment_log('需要先在 Cloudflare 启用 Workers Analytics Engine。启用后到“已有网站”选择这个站点，点击“更新所选网站程序”继续；不会重建 R2。')
                        try:
                            self.refresh(row['id'])
                            if host in getattr(self,'sites',{}):
                                self.site.set(host);self.load_site()
                        except Exception as refresh_error:
                            self._deployment_log('刷新本机站点列表失败：'+str(refresh_error))
                    else:self.status.set(error)
                self.after(0,failed)
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

    def toggle_secret_visibility(self):
        mask='' if self.show_secrets.get() else '•'
        self.secret_entry.configure(show=mask)
        self.password_entry.configure(show=mask)

    def credential_target(self):
        try:
            account=self.selected()
            selected=self.site.get()
            site=next((item for item in known_gallery_sites(load_cf_state())
                       if item['host']==selected and item.get('account_id')==account['account_id']),None)
            if not site or normalize_host(self.domain.get()) != site['host']:
                raise ValueError('请先从站点列表选择要修改的站点，域名须与所选站点一致。')
            return site
        except Exception as exc:
            messagebox.showerror('请选择站点',str(exc),parent=self)
            return None

    def generate_secret(self):
        from promo_deploy import generate_publish_secret
        self.publish_secret.set(generate_publish_secret())
        self.status.set('新密钥仅填入编辑框，尚未修改线上站点。点击“修改 / 应用发布密钥”才生效。')

    def view_credentials(self):
        site=self.credential_target()
        if not site:return
        win=tk.Toplevel(self);win.title('查看本机保存的站点凭据');win.transient(self)
        win.geometry('680x420')
        ttk.Label(win,text=site['host']+' · '+('成品库' if site.get('kind')=='summary' else '帖文图库')).pack(anchor='w',padx=16,pady=10)
        ttk.Label(win,text='这里显示本机保存的值；若其他电脑修改过，请导入最新配置。',wraplength=640).pack(anchor='w',padx=16)
        pending=(load_cf_state().get('pending_credentials') or {}).get(site['host']) or {}
        entries=[]
        for label,key in [('已保存访问密码','access_password'),('已保存发布密钥','secret')]:
            value=site.get(key)
            note=('（线上密码保留，但 Cloudflare 不会返回其值）' if key=='access_password' and not site.get('access_password_known',True)
                  else '（本机无记录）' if value is None else '（无需密码）' if key=='access_password' and value=='' else '')
            ttk.Label(win,text=label+note).pack(anchor='w',padx=16,pady=(8,0))
            variable=tk.StringVar(win,value=value or '')
            entry=ttk.Entry(win,textvariable=variable,state='readonly',show='•');entry.pack(fill='x',padx=16)
            entry._value=variable;entries.append(entry)
            if key in pending:
                ttk.Label(win,text='待确认修改（尚未验证生效）').pack(anchor='w',padx=16)
                variable=tk.StringVar(win,value=pending[key])
                entry=ttk.Entry(win,textvariable=variable,state='readonly',show='•');entry.pack(fill='x',padx=16)
                entry._value=variable;entries.append(entry)
        visible=tk.BooleanVar(win,value=False)
        ttk.Checkbutton(win,text='显示密码和密钥',variable=visible,
            command=lambda:[entry.configure(show='' if visible.get() else '•') for entry in entries]).pack(anchor='w',padx=16,pady=8)
        ttk.Button(win,text='关闭',command=win.destroy).pack(anchor='e',padx=16,pady=8)

    def reset_password(self):
        from gallery_management import change_access_password
        site=self.credential_target()
        if not site:return
        password=self.password.get()
        repeated=simpledialog.askstring('再次输入访问密码',f"目标站点：{site['host']}\n再次输入新访问密码（留空表示无需密码）：",show='•',parent=self)
        if repeated is None:return
        if repeated!=password:
            messagebox.showerror('密码不一致','两次输入不一致，未修改站点。',parent=self);return
        if not messagebox.askyesno('确认修改访问密码',
            f"目标站点：{site['host']}\n将更新原项目，使新访问密码生效。"+
            ('\n新密码为空，网站将无需密码访问。' if not password else '\n访客需要使用新密码重新登录。'),parent=self):return
        self._site_action(lambda row,host:change_access_password(CloudflareAPI(row['api_token']),row['account_id'],host,password,
            log=lambda message:self.after(0,lambda text=message:self.status.set(text))))

    def restore_secret(self):
        from gallery_management import change_publish_secret
        site=self.credential_target()
        if not site:return
        secret=self.publish_secret.get().strip()
        if len(secret)<24:
            messagebox.showerror('发布密钥过短','发布密钥至少 24 个字符，可以点击“生成新发布密钥”。',parent=self);return
        repeated=simpledialog.askstring('再次输入发布密钥',f"目标站点：{site['host']}\n再次输入新发布密钥：",show='•',parent=self)
        if repeated is None:return
        if repeated.strip()!=secret:
            messagebox.showerror('密钥不一致','两次输入不一致，未修改站点。',parent=self);return
        if not messagebox.askyesno('确认修改发布密钥',
            f"目标站点：{site['host']}\n将应用新发布密钥，并同步本机绑定的发布配置。\n"
            '其他维护电脑需导入更新后的配置才能发布。网站素材和访问密码保留。',parent=self):return
        self._site_action(lambda row,host:change_publish_secret(CloudflareAPI(row['api_token']),row['account_id'],host,secret,
            log=lambda message:self.after(0,lambda text=message:self.status.set(text))))

    def deploy(self,create=False,update=False):
        if getattr(self,'_site_busy',False):return
        kind='summary' if self.kind.get()=='多工作表图库' else 'promo'
        password=self.password.get()
        if create:
            current=next((m for m in self.app._menus if m['id']==self.app._active_menu_id),{})
            if current.get('template') not in ('filter','worksheet_merge'):
                messagebox.showerror('部署类型','请先选择贴文筛选汇总或多工作表汇总配置。',parent=self);return
            self.create_menu_id=current['id']
            kind='summary' if current.get('template')=='worksheet_merge' else 'promo'
            password=self.new_password.get()
            try:
                from cf_accounts import require_menu
                require_menu(self.selected()['id'],self.create_menu_id)
            except Exception as exc:
                messagebox.showerror('账号授权',str(exc),parent=self);return
            custom=self.use_custom_domain.get()
            host=normalize_host(self.new_domain.get()) if custom else ''
            if custom and (not host or '.' not in host or host.endswith('.pages.dev')):
                messagebox.showerror('自定义域名','请填写自己的域名；使用 Cloudflare 地址时取消勾选即可。',parent=self);return
            project=self.project_name.get().strip()
            if not custom and not project:
                import secrets
                project=('summary-' if kind=='summary' else 'gallery-')+secrets.token_hex(6)
                self.project_name.set(project)
            destination=host if custom else project+'.pages.dev'
            if not messagebox.askyesno('创建新站点',f'将创建独立站点：{destination}\n'+
                ('使用所填自定义域名。' if custom else '使用 Cloudflare 地址，无需购买域名或配置 DNS。'),parent=self):return
        else:
            site=self.credential_target()
            if not site:return
            host=site['host'];project='';kind=site.get('kind','promo')
            if update and not messagebox.askyesno('更新原站点',f"更新 {host} 的网页程序，保留原项目、素材、密码和密钥。",parent=self):return
        self._site_action(lambda row,host:deploy_gallery(host,token=row['api_token'],account_id=row['account_id'],profile_id=row['id'],
            site_kind=kind,access_password=password,force_update=update,create_if_missing=create,project_name=project,
            log=lambda message:self.after(0,lambda text=message:self._deployment_log(text))),target_host=host,allow_empty=create,bind_menu_id=self.create_menu_id if create else None)

    def deployed(self,identity,host):
        self.refresh(identity);self.site.set(host);self.load_site()
        from gallery_management import sync_publish_settings
        site=getattr(self,'sites',{}).get(host)
        if site and site.get('secret'):
            # Update memory as well as the on-disk configuration, so a later Save
            # cannot put the previous key back into a bound menu.
            for menu in self.app._menus:
                sync_publish_settings(menu.get('settings') or {},site,site['secret'])
            cfg=self.app.cfg
            values={'cf_profile_id':cfg.cf_profile_id,'cf_publish_url':cfg.cf_publish_url}
            if sync_publish_settings(values,site,site['secret']):cfg.cf_publish_secret=site['secret']
            live={'cf_profile_id':getattr(self.app,'_filter_cf_profile_id',''),
                  'cf_publish_url':self.app.var_cf_url.get()}
            if sync_publish_settings(live,site,site['secret']):self.app.var_cf_secret.set(site['secret'])
        self.status.set('站点已就绪：'+(site.get('site_url') or 'https://'+host if site else 'https://'+host)+'。可绑定到当前配置，日常点击“发布图库”。')
