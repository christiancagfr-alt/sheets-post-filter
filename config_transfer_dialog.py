"""Desktop configuration transfer UI; local I/O only, no publishing side effects."""
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from dataclasses import asdict

from config_transfer import export_bundle, read_bundle, import_bundle, summarize_bundle
from fetch_posts import SCRIPT_DIR, load_config, save_config
from promo_deploy import load_cf_state


class ConfigTransferDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app=app
        self.title('配置导出 / 导入')
        self.geometry('680x610')
        self.configure(bg='#f1f5f9')
        self.minsize(620,520)
        self.transient(app)
        self.grab_set()
        panel=ttk.Frame(self,padding=20);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='跨电脑迁移，也可让多人维护同一个图库',font=('',12,'bold')).pack(anchor='w')
        ttk.Label(panel,text='包含表格配置、菜单编号、Cloudflare 账号与站点及发布密钥。\n用迁移密码加密，导入后在新电脑重新加密保存，不会重新部署。\n请只交给可信的维护人员；迁移文件和密码不要一起发送。',wraplength=570).pack(anchor='w',pady=12)
        ttk.Label(panel,text='选择要导出的配置（可多选；只包含关联的站点与账号）').pack(anchor='w')
        menu_frame=ttk.Frame(panel);menu_frame.pack(fill='both',expand=True,pady=(6,8))
        self.menu_list=tk.Listbox(menu_frame,selectmode='multiple',exportselection=False,height=7,
            bg='#ffffff',fg='#1e293b',selectbackground='#2563eb',selectforeground='#ffffff',
            activestyle='dotbox',font=('',11))
        scrollbar=ttk.Scrollbar(menu_frame,orient='vertical',command=self.menu_list.yview)
        self.menu_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.menu_list.pack(side='left',fill='both',expand=True)
        self.menu_ids=[menu['id'] for menu in app._menus]
        for index,menu in enumerate(app._menus):
            self.menu_list.insert('end',menu['name'])
            if menu['id']==app._active_menu_id:self.menu_list.selection_set(index)
        choose=ttk.Frame(panel);choose.pack(fill='x',pady=(0,8))
        ttk.Button(choose,text='全选',command=lambda:self.menu_list.selection_set(0,'end')).pack(side='left')
        ttk.Button(choose,text='清空选择',command=lambda:self.menu_list.selection_clear(0,'end')).pack(side='left',padx=8)
        self.credentials=tk.BooleanVar(value=True)
        ttk.Checkbutton(panel,text='导出时包含已选服务账号（Google 表格访问凭据）',variable=self.credentials).pack(anchor='w')
        self.export_button=ttk.Button(panel,text='导出加密配置…',command=self.export)
        self.export_button.pack(anchor='w',pady=(12,4))
        self.import_button=ttk.Button(panel,text='导入加密配置…',command=self.read)
        self.import_button.pack(anchor='w',pady=4)
        self.status=tk.StringVar()
        ttk.Label(panel,textvariable=self.status,wraplength=570).pack(anchor='w',pady=8)
        self.protocol('WM_DELETE_WINDOW',lambda:None if getattr(self,'busy',False) else self.destroy())

    def _work(self, operation, done):
        self.busy=True
        for button in (self.export_button,self.import_button):button.configure(state='disabled')
        self.status.set('正在处理加密配置…')
        def run():
            try:
                result=operation()
                self.after(0,lambda:self._completed(result,done))
            except Exception:
                # JSON/crypto exceptions can include sensitive input. Keep errors generic.
                self.after(0,self._failed)
        threading.Thread(target=run,daemon=True).start()

    def _completed(self,result,done):
        self._idle()
        done(result)

    def _failed(self):
        self._idle()
        if getattr(self,'_timers_paused',False):
            self._timers_paused=False
            self.app._sync_schedulers()
        self.status.set('未完成：请检查迁移密码、文件格式、权限及服务账号文件。本机配置已保留。')

    def _idle(self):
        self.busy=False
        for button in (self.export_button,self.import_button):button.configure(state='normal')

    def export(self):
        selected_ids=[self.menu_ids[int(i)] for i in self.menu_list.curselection()]
        if not selected_ids:
            messagebox.showwarning('选择配置','请先勾选 / 选中至少一个要导出的配置。',parent=self);return
        path=filedialog.asksaveasfilename(parent=self,title='导出加密配置',defaultextension='.dsconfig',
            filetypes=[('加密迁移配置','*.dsconfig')],initialfile='数据汇总工具配置.dsconfig')
        if not path:return
        password=simpledialog.askstring('迁移密码','设置至少 10 个字符的迁移密码（遗忘无法恢复）：',show='•',parent=self)
        if not password:return
        if len(password)<10:
            messagebox.showwarning('迁移密码','至少需要 10 个字符。',parent=self);return
        again=simpledialog.askstring('确认迁移密码','再次输入迁移密码：',show='•',parent=self)
        if again!=password:
            messagebox.showwarning('迁移密码','两次输入不一致，未导出。',parent=self);return
        config=asdict(self.app._cfg_for_action())
        include=self.credentials.get()
        self._work(lambda:export_bundle(path,password,config,load_cf_state(),include_credentials=include,menu_ids=selected_ids),
            lambda result:self.status.set(f"已加密导出：{result['menus']} 个配置、{result['sites']} 个站点、{result['credentials']} 个服务账号。"))

    def read(self):
        path=filedialog.askopenfilename(parent=self,title='导入加密配置',filetypes=[('加密迁移配置','*.dsconfig')])
        if not path:return
        password=simpledialog.askstring('迁移密码','输入导出时设置的迁移密码：',show='•',parent=self)
        if not password:return
        self._work(lambda:read_bundle(path,password),self.confirm_import)

    def confirm_import(self,payload):
        from app import job_snapshot, stop_all_menu_schedulers
        summary=summarize_bundle(payload)
        if any(job_snapshot(key).get('running') or job_snapshot(key).get('queued')
               for key in ['default']+[menu['id'] for menu in self.app._menus]):
            self.status.set('请先停止并等待现有任务结束，再导入配置。');return
        if not messagebox.askyesno('确认导入',
            f"导入 {summary['menus']} 个配置、{summary['sites']} 个站点、{summary['credentials']} 个服务账号。\n"
            '同编号配置将替换，其他本机配置保留；先自动备份。导入的定时任务暂停，核对后可重新启用。\n'
            '不会创建、部署或发布任何站点。是否继续？',parent=self):return
        # Snapshot unsaved edits before merging. Stop local timers while files/UI reload.
        save_config(self.app._cfg_for_action())
        stop_all_menu_schedulers()
        self._timers_paused=True
        self._work(lambda:import_bundle(payload,SCRIPT_DIR),self.imported)

    def imported(self,result):
        app=self.app
        self._timers_paused=False
        if app._settings_win is not None and app._settings_win.winfo_exists():
            app._settings_win.destroy();app._settings_win=None;app._settings_list=None
        if app._selection_after is not None:
            app.after_cancel(app._selection_after);app._selection_after=None
        app._pending_select=None
        app._active_menu_id=''
        app._page_settings.clear();app._menu_scroll.clear()
        app.cfg=load_config()
        app._init_menus();app._load_global_credentials();app._sync_schedulers()
        self.status.set('导入成功。已保留原站点与菜单编号；核对配置后直接发布图库，无需部署。')
        app._append_log('配置已导入；原配置已在本机 private-backup 中备份。导入的定时任务暂停。')
