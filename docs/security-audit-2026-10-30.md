# 安全审核与界面改进记录

日期：2026-10-30。版本号保留 1.5.16。本次没有提交、推送、发布 Release、安装覆盖原程序或更新线上站点。

## 1. 项目概况和审核范围

Python 3.12；Tkinter 桌面界面、Flask 本机备用界面、gspread/Google 服务账号；原生 JavaScript 图库、Cloudflare Pages/Worker/R2。另有历史 Vercel/Pages Functions 和 Apps Script 兼容文件。包管理器为 pip；前端无 npm 第三方依赖。构建使用 PyInstaller、Inno Setup，CI 使用 GitHub Actions。

覆盖应用、前端、模板、测试、示例、部署脚本、CI、本机敏感配置、Git 历史和打包资源选取。初始清单为 100 份文本文件、36,128 行，新增安全模块另行补审。所有 Python 应用模块进行了 AST/模式检查和 Bandit；重点人工追踪凭据、网络、鉴权、HTML 插入、发布及构建入口。上游库按实际版本、公告和漏洞库核查，不声称逐行审计了第三方依赖。

原仓库及旧历史保留为本机私有备份；`public-export/` 为独立、无 Git 历史的脱敏发布副本。只能从干净副本准备公开发布，不能将原历史、运行配置和旧构建资料合并进去。

## 2. 问题汇总

严重级别按本项目风险判断。“已修复”指本地源码；网站侧修复尚未部署。

| 编号 | 类别 | 严重级别 | 位置 | 状态 |
| --- | --- | --- | --- | --- |
| DEP-01 | 依赖 | High | requirements.txt、requirements.lock | 已修复：4 个包、8 项去重公告 |
| DEP-02 | 依赖 | Medium | requirements-build.txt | 已修复：setuptools 84.1.0，兼容性和构建通过 |
| WEB-01 | 代码 | High | app.py:63 | 已修复：本机接口跨站调用和 Host 重绑定入口 |
| NET-01 | 代码 | High | promo_deploy.py:309；publish_cloudflare.py:470 | 已修复：带密钥请求自动跳转和目的地址约束不足 |
| NET-02 | 代码 | Medium | video_duration.py:403；public_urls.py:8 | 已修复已知文件/内网入口；DNS 边界仍建议关注 |
| NET-03 | 代码 | High | promo-site/functions/api/drive-cache.js:16；functions/_lib/safe-upstream.js:2 | 已修复：任意上游代理、跳转绕过来源校验 |
| AUTH-01 | 代码 | High | promo-site/gallery-worker.js:276 及 CDN/图片路由 | 已修复：设置密码后仍可绕过这些入口的鉴权 |
| AUTH-02 | 代码 | Medium | promo-site/functions/_lib/auth.js:53；promo-site/api/auth.js:43 | 已修复：Cookie 缺少服务端签名有效期 |
| AUTH-03 | 代码 | High | promo-site/google-apps-script/google-apps-script-water.gs:1197 | 已修复：未配置密钥时默认放行 |
| CACHE-01 | 代码 | Medium | 历史 drive-cache 接口 | 已修复：受密码保护的数据仍允许共享缓存 |
| DOS-01 | 代码 | Medium | summary-gallery/gallery-worker.js:26；promo-site/gallery-worker.js:86 | 已修复：解析前按流限制字节数 |
| XSS-01 | 代码 | Low | summary-gallery/index.html:2783 | 已修复：素材 ID 写入属性时转义；原发布 ID 校验限制了利用条件 |
| SEC-01 | 密钥 | Medium | fetch_posts.py:load_config/save_config；promo_deploy.py:78/94 | 已修复：DPAPI、原子保存、解密失败保留原文件 |
| SEC-02 | 密钥 | Medium | 本机旧发布密钥文本 | 已修复：批准后加密归档并删除明文副本 |
| DATA-01 | 密钥/隐私 | High | 旧默认值、历史脚本、案例、Git 历史 | 发布副本已脱敏；原历史只保留本机，远端既有副本待确认 |
| PKG-01 | 代码/隐私 | High | promo_deploy.py:238；sheets_post_filter.spec:39 | 已修复：宽泛资源收集可能带入隐藏文件和缓存 |
| CI-01 | 代码 | Medium | .github/workflows/release.yml | 已修复：可变 Action 标签、过宽权限、构建时额外下载语言文件 |
| CI-02 | 依赖/维护 | Low | macOS Intel runner | 已改 macos-15-intel；实际 macOS 构建待 CI 验证 |
| PROC-01 | 代码 | Low | desktop_app.py:_kill_stale_mutex | 已修复：共享端口的其他 Python 进程可能被误判为残留；结束前核对脚本路径，查询失败时不结束 |

## 3. 依赖核查

目前明确声明 5 个应用直接依赖及 4 个安全约束；`requirements.lock` 锁定 Windows/Python 3.12 的 24 个运行时包，`requirements-build.txt` 另锁定 7 个构建包。macOS 条件依赖和安装结果需对应平台验证。

| 包 | 审核前实际版本 | 当前锁定版本 | 说明 |
| --- | --- | --- | --- |
| gspread | 6.2.1 | 6.2.1 | 直接：Google Sheets |
| google-auth | 2.56.2 | 2.56.2 | 直接：服务账号 |
| Flask | 3.1.3 | 3.1.3 | 直接：本机网页 |
| blake3 | 1.0.9 | 1.0.9 | 直接：Pages 散列 |
| requests | 2.34.2 | 2.34.2 | 直接：补充明确声明 |
| google-auth-oauthlib | 1.4.0 | 1.4.0 | 间接；扫描未命中 |
| pyasn1_modules | 0.4.2 | 0.4.2 | 间接；扫描未命中 |
| cryptography | 50.0.2 | 50.0.2 | 间接；扫描未命中 |
| blinker | 1.9.0 | 1.9.0 | 间接；扫描未命中 |
| click | 8.4.2 | 8.4.2 | 间接；扫描未命中 |
| itsdangerous | 2.2.0 | 2.2.0 | 间接；扫描未命中 |
| Jinja2 | 3.1.6 | 3.1.6 | 间接；扫描未命中 |
| MarkupSafe | 3.0.3 | 3.0.3 | 间接；扫描未命中 |
| Werkzeug | 3.1.8 | 3.1.9 | Windows 路径处理补丁 |
| requests-oauthlib | 2.0.0 | 2.0.0 | 间接；与 OAuthlib 4 验证兼容 |
| pyasn1 | 0.6.3 | 0.6.4 | ASN.1 资源消耗补丁 |
| cffi | 2.0.0 | 2.0.0 | 间接；扫描未命中 |
| colorama | 0.4.6 | 0.4.6 | 间接；扫描未命中 |
| oauthlib | 3.3.1 | 4.0.0 | 用户批准大版本升级 |
| pycparser | 3.0 | 3.0 | 间接；扫描未命中 |
| charset-normalizer | 3.4.7 | 3.4.7 | 间接；扫描未命中 |
| idna | 3.11 | 3.15 | Unicode 域名资源消耗补丁 |
| urllib3 | 2.8.0 | 2.8.0 | 已核对相关公告；扫描未命中 |
| certifi | 2026.10.23 | 2026.10.23 | CA 包；扫描未命中 |
| PyInstaller | 6.20.0 | 6.20.0 | 构建；扫描未命中 |
| pyinstaller-hooks-contrib | 2026.4 | 2026.4 | 构建；扫描未命中 |
| altgraph | 0.17.5 | 0.17.5 | 构建；扫描未命中 |
| packaging | 23.2 | 23.2 | 构建；扫描未命中 |
| pefile | 2024.8.26 | 2024.8.26 | Windows 构建；扫描未命中 |
| pywin32-ctypes | 0.2.3 | 0.2.3 | Windows 构建；扫描未命中 |
| setuptools | 81.0.0 | 84.1.0 | 用户批准升级；构建通过 |

逐包检索公开安全和维护信息，与 pip-audit 的 PyPI、OSV 结果交叉核对。重复记录按 GHSA/CVE 别名去重。“未命中”只代表本次版本及数据库快照，不保证不存在其他漏洞。

具体公告、修复和利用条件：

- Werkzeug：Windows `safe_join` 特殊设备路径问题与本机 Flask 静态资源相关，升级 3.1.9，并补充接口边界。[官方公告](https://github.com/pallets/werkzeug/security/advisories/GHSA-g6x2-hccm-hh4m)
- pyasn1：OID、REAL、长标签解析的资源消耗问题。未发现通用 ASN.1 服务端入口，仍升级 0.6.4，消除依赖链处理异常输入的风险。[OID](https://github.com/advisories/GHSA-8ppf-4f7h-5ppj)、[REAL](https://github.com/advisories/GHSA-hm4w-wwcw-mr6r)、[标签](https://github.com/advisories/GHSA-m4p7-r5rc-7g4j)
- OAuthlib：JSONP 撤销响应注入、PKCE 时序、设备授权校验。未发现项目提供这些 OAuth 服务端入口；按用户决定升级 4.0.0，并验证客户端及服务账号流程。[JSONP](https://github.com/oauthlib/oauthlib/security/advisories/GHSA-hj66-6f7g-4r5v)、[PKCE](https://github.com/oauthlib/oauthlib/security/advisories/GHSA-xpv3-w29h-x7cv)、[设备授权](https://github.com/oauthlib/oauthlib/security/advisories/GHSA-3jp2-3mrg-2vxc)
- idna：特制 Unicode 主机名可能造成资源消耗，锁定 3.15，并限制凭据请求的地址长度和主机格式。[官方公告](https://github.com/kjd/idna/security/advisories/GHSA-65pc-fj4g-8rjx)
- setuptools：源码分发的排除规则规范化问题，以及 Windows 旧 wheel 安装路径越界。项目不使用 sdist 发布，也不调用旧安装入口；仍升级 84.1.0，并验证真实 `FileList.exclude/global_exclude` 的 NFC/NFD 排除行为。首项官方补丁字段未填版本，漏洞库标记 83.0.0 修复，因此另做行为验证。[规范化](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c)、[Windows](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w)

未确认仿冒包或正式停止维护的应用依赖。google-auth 旧独立仓库归档是迁移到 google-cloud-python，不能据此认定弃用。requests-oauthlib、itsdangerous、colorama 等发布频率较低，建议持续监测；久未发布本身不证明不安全。[迁移说明](https://github.com/googleapis/google-auth-library-python)

## 4. 具体改动、原因与验证

### 本机、网络和网站

恶意网页原可尝试用简单请求提交 JSON，或以 DNS rebinding 调用本机控制接口。现在验证 Host、Origin、Fetch Metadata，修改接口要求 JSON，增加大小限制、禁止嵌入和禁止缓存；仍只监听回环地址，不增加登录操作。

Cloudflare 管理令牌仅发往官方 HTTPS API；携带发布密钥的请求不自动跳转。旧发布接口错误不再回显上游原文。自动读取视频网页拒绝文件协议、本机及当前解析出的私有地址，并限制 HTML 读取量。

历史 Pages Functions 只允许 Google 或部署环境明确配置的 HTTPS 缓存来源，每次跳转重新校验。旧 Apps Script 未设置密钥时不再放行。这些兼容修复不会启用 Apps Script，也未替换现有桌面直接发布流程。

设置密码时，旧 Worker CDN、图片及相应 Functions 入口也须鉴权；受保护响应不进共享缓存。旧 Cookie 增加 HMAC 签名的服务端有效期，部署后旧会话需重新登录。未设置密码的公开访问语义保留。

两套 Advanced Mode Worker 在解析前按流限制请求字节数：新图库普通操作 64 KiB，发布 3 MiB。素材 ID 写入 HTML 属性时转义；标题等原有转义保留。

### 凭据和隐私

Cloudflare 令牌、发布密钥、站点密码及嵌套对应字段使用当前 Windows 用户 DPAPI。保存采用同目录临时文件、刷盘、原子替换；解密失败报错并保留文件，不能当作空配置覆盖。Cloudflare 状态合并加锁。

已核对三份本机配置，19 个敏感字段受保护，解密后配置值一致；旧密钥文本经用户批准加密归档并删除明文。测试账户与实际账户不同的问题已处理，最终在实际账户下验证可读。没有轮换在线密钥，也没有重写外部 Google 服务账号 JSON。[DPAPI 说明](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

发布副本排除运行配置、账号文件、旧缓存、实际表格/部署 ID、人员和分类案例、旧 Git 历史。原私有资料仍保留本机。静态上传和打包改为资源允许清单，排除隐藏目录、日志、私钥和发布测试请求。

`tools/check_public_release.py` 检查公开目录标记、敏感文件、Google ID、常见令牌、服务账号邮箱和凭据字面量。原私有仓库已启用 pre-push；因没有公开导出标记，普通推送会被阻止。检查可以被主动绕过，不能替代新增内容的人工复核。

CI Action 固定为官方 API 核对的提交 SHA；默认令牌只读，构建证明和 Release 分任务授予权限；安装器语言文件改用仓库内副本。停用的 Intel runner 改为受支持标签。[官方 runner 信息](https://github.com/actions/runner-images)

### 界面与模板

新配置从独立默认值创建，不复制菜单、账号、链接、分类或定时开关。列字母、行号、内置时间规则等结构默认保留。贴文模板的硬编码链接回填已移除；桌面和备用网页新增配置均不复制旧数据。

桌面改为蓝灰配色，统一表单、按钮、提示和日志颜色；缩减头部及按钮留白，增加 Ctrl+S 和表单滚轮。页面复用控件，合并布局更新、批量追加日志，定时状态按文件变化缓存；每个配置保留滚动位置并显示自己的日志。快速点击只应用最后选择，避免将旧页面字段存到新配置。

日期匹配、去重、分类计数、写入顺序、汇总和发布规则未修改。

### 检查结果

- 隔离 Python 3.12 环境安装锁定依赖，`pip check` 通过。
- 176 项 Python 测试通过，包含空模板、跨站/Host 边界、DPAPI、OAuth 客户端、临时生成密钥的服务账号刷新，以及原汇总测试；没有访问真实 Google 账号。
- 29 项 Node 测试通过，包含缓存、日期规则、分页、分片、鉴权、下载、会话有效期、请求上限和跳转地址校验。
- Tk 隔离测试通过：四类空模板、快速切换、控件复用、独立列/行、视频日志、三通道和原配置保留。50 行合成配置切换中位数：强制重建约 12.3 ms，复用约 4.0 ms；不代表网络汇总速度提升。
- PyPI 和 OSV 对 24 个运行时锁定包复扫未报告已知漏洞；额外 7 个构建包复扫也未命中。
- Bandit 检查 23 个应用模块，无 High；剩余 61 个 Low 和 1 个 Medium 已分类，主要为容错、常量遮罩、系统工具调用、重试抖动。Medium 是固定官方 HTTPS 更新地址的 urlopen 提示，不能直接认定任意协议漏洞。
- Windows PyInstaller 生成了实际 EXE；没有安装覆盖现有软件。发布副本单独重新测试和检查；原私有仓库的发布检查预期拒绝。
- 对构建产物解包检查应用模块的全部字符串常量：本机敏感值匹配为 0，私有配置/历史资源文件为 0。此检查不能代替未来发布前的复核。
- 未发现 SQL/NoSQL、eval、pickle、用户上传解压入口；无 Electron/WebView IPC 或浏览器扩展权限模型。系统命令为参数数组、未使用 shell=True；TLS 校验未关闭。重试用 random 不承担密钥生成，散列/会话使用标准算法。

## 5. 待确认、剩余风险和建议

| 事项 | 方案、建议与状态 |
| --- | --- |
| 大版本升级 | 用户已批准 OAuthlib 4.0.0、setuptools 84.1.0，兼容和构建验证完成。 |
| Windows 加密 | 已执行；换电脑/账户后需重新登录或填写密钥。未升级的软件进程若仍在运行，需关闭后重新启动，避免旧代码再次明文保存。 |
| 原历史 | 用户已选择保留本机私有备份、创建无历史副本，已执行；本次未重写远端历史。 |
| 远端既有提交、旧 Release、派生仓库 | 干净目录不能撤回既有公开内容。是否需要清理须确认真实暴露情况；本次没有远程删除、强推。待确认。 |
| 服务账号 JSON | 保留外部文件选择及 Google 原有格式。建议限制文件目录权限，不上传、不分发；未擅自更换账号。 |
| macOS 与线上 | Windows 不能证明 macOS 原生构建、线上部署配置已生效；须在对应平台验证，网站源码修复需另行部署。待确认。 |
| 共享密码与限速 | 建议按实际 Cloudflare 计划配置登录限速；个人身份登录会改变产品权限模式，须后续决定。 |
| 视频网页 DNS 边界 | 已限制协议、当前解析地址和跳转；解析到连接之间仍有重绑定边界。完整固定连接需验证代理兼容性，未声称消除所有 SSRF。建议关注。 |
| 旧兼容部署与 R2 | 是否仍启用 Vercel/Apps Script、是否存在直接公开 R2 出口，不能仅凭本地源码确认；建议核对、停用不用的部署。本次未擅自删功能或改线上资源。 |
| 供应链 | 版本已锁定，建议进一步为跨平台包加散列、持续扫描；Python/Node/安装器/CI 镜像须持续维护。 |

未在当前受控源码或本次检查到的 Git 文本历史发现匹配到的实际私钥/当前令牌；确实发现个人表格和站点信息。此结论不涵盖未获取的远端旧二进制、缓存和派生仓库。凡是曾提交、公开、发给他人或不能排除泄露的密钥，必须到平台作废并重新生成；删代码、加密本机文件和新建目录都不能代替撤销旧密钥。

本报告不含实际账号、密码、表格 ID、个人分类或人员案例。详细扫描证据仅保留本机，不随公开源码上传。
