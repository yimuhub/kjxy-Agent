//! 科技小院桌面壳：本地引擎 sidecar 生命周期 + splash + 双模式（本地/连接服务器）。
//!
//! 设计蓝本：agency-orchestrator 的桌面壳模式（splash 内联 HTML、健康轮询、
//! 端口顺延、级联退出、失败给原生对话框而非白屏），用 Tauri 2 实现：
//! - 引擎是随包 sidecar（backend/standalone.py 打出的 PyInstaller 二进制），
//!   只监听 127.0.0.1；壳只负责拉进程、等就绪、把窗口指过去。
//! - 复用：启动先探 18700-18710 上是否已有健康引擎，有则直接接管（修"关窗再开报错"）。
//! - 退出级联：ExitRequested 时杀 sidecar，避免"关窗后引擎还在"。
//! - remote 模式（<data>/client.json 配 {"mode":"remote","url":...}）：不拉引擎，
//!   窗口直接加载服务器地址（移动云端同款的桌面逃生口）。
//!
//! 编译提示：本文件已用本地 cargo check（tauri 2.12 / 各插件 2.x，2026-10）验证编译，
//! 但运行时行为（splash 导航、updater 下载安装）未在真机构建产物上回归；不确定处以
//! TODO 注释标明。

use std::fs;
use std::io::Read;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use serde::Deserialize;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

/// 引擎探测/绑定端口段（AO 同款端口顺延，避开常用 8xxx 冲突面）
const PORT_RANGE: std::ops::RangeInclusive<u16> = 18700..=18710;
/// splash 轮询健康上限（秒）：PyInstaller 首启解压 + 迁移 + seed，慢机器要留足
const HEALTH_TIMEOUT_SECS: u64 = 90;

struct EngineState {
    child: Mutex<Option<CommandChild>>,
    port: Mutex<Option<u16>>,
}

/// 结束引擎进程树：PyInstaller onefile 会派生一个子进程，只杀父进程会留下监听端口。
fn stop_engine(child: CommandChild) {
    let pid = child.pid().to_string();
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-TERM", "-P", &pid])
            .status();
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .status();
    }
    let _ = child.kill();
}

/// 启动后静默检查更新的延迟：不抢启动体验（引擎拉起/splash 优先）
const UPDATE_CHECK_DELAY_SECS: u64 = 30;

/// 缓存 check 命中的 Update，供 download_and_install 直接复用（不重复拉清单）
struct UpdateState {
    pending: Mutex<Option<tauri_plugin_updater::Update>>,
}

/// 发给前端的轻量版本信息（Update 本身持有网络客户端，不可序列化）
#[derive(Clone, serde::Serialize)]
struct UpdateAvailable {
    version: String,
    notes: Option<String>,
}

/// 向窗口注入底部提示条（splash/主窗/引擎页通吃），纯 eval 不依赖页面 IPC
fn inject_update_banner(win: &tauri::WebviewWindow, version: &str) {
    let js = format!(
        "(function(){{if(!document.body)return;var i='kjxy-update-banner';if(document.getElementById(i))return;var d=document.createElement('div');d.id=i;d.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:8px 16px;background:#1c2430;color:#e6edf3;font:13px -apple-system,\"PingFang SC\",\"Microsoft YaHei\",sans-serif;text-align:center';d.textContent='有新版本 v{v} 可用：在设置页/版本条点击「检查更新」完成升级';document.body.appendChild(d);}})()",
        v = version
    );
    let _ = win.eval(&js);
}

/// 记录新版本并双路通知：广播事件（供前端 listen）+ 注入提示条（供无 JS 集成的页面兜底）
fn publish_update(
    app: &tauri::AppHandle,
    update: tauri_plugin_updater::Update,
) -> UpdateAvailable {
    let info = UpdateAvailable {
        version: update.version.clone(),
        notes: update.body.clone(),
    };
    {
        let st = app.state::<UpdateState>();
        *st.pending.lock().unwrap() = Some(update);
    }
    let _ = app.emit("app:update-available", info.clone());
    for win in app.webview_windows().values() {
        inject_update_banner(win, &info.version);
    }
    info
}

/// 启动后延迟静默检查：不弹窗、失败只打日志（设置页可手动再查），发现新版走 publish_update
fn spawn_update_watcher(app: tauri::AppHandle) {
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(UPDATE_CHECK_DELAY_SECS));
        // 独立线程内 block_on 轮询清单请求（异步 API 在 tokio 上跑，不占主线程）
        let outcome = app
            .updater()
            .map_err(|e| e.to_string())
            .and_then(|updater| {
                tauri::async_runtime::block_on(updater.check()).map_err(|e| e.to_string())
            });
        match outcome {
            Ok(Some(update)) => {
                publish_update(&app, update);
            }
            // 已是最新：静默
            Ok(None) => {}
            // 网络失败等：不打扰用户，下轮启动再说
            Err(e) => eprintln!("[updater] 静默检查跳过：{e}"),
        }
    });
}

#[derive(Deserialize, Default)]
struct ClientConfig {
    #[serde(default)]
    mode: String, // "" | "local" | "remote"
    #[serde(default)]
    url: String,
}

/// 与 standalone.py 保持一致的数据目录（壳显式传 TECHYARD_DATA_DIR，不赌默认值）
fn data_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    // 测试/冒烟脚本可显式指定独立数据目录，避免污染真实用户数据
    if let Ok(raw) = std::env::var("TECHYARD_DATA_DIR") {
        let trimmed = raw.trim().to_string();
        if !trimmed.is_empty() {
            let dir = PathBuf::from(trimmed);
            fs::create_dir_all(&dir).map_err(|e| format!("创建数据目录失败：{e}"))?;
            return Ok(dir);
        }
    }
    let base = dirs::data_dir().ok_or("无法解析系统数据目录")?;
    let dir = base.join("科技小院");
    fs::create_dir_all(&dir).map_err(|e| format!("创建数据目录失败：{e}"))?;
    let _ = app;
    Ok(dir)
}

fn client_config(dir: &PathBuf) -> ClientConfig {
    fs::read_to_string(dir.join("client.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// 首次启动生成并持久化桌面实例专属 JWT 密钥（与服务器部署完全隔离）
fn ensure_secret(dir: &PathBuf) -> Result<String, String> {
    let path = dir.join("secret.key");
    if let Ok(existing) = fs::read_to_string(&path) {
        let trimmed = existing.trim().to_string();
        if trimmed.len() >= 48 {
            return Ok(trimmed);
        }
    }
    // 无 rand crate：借系统熵源拼一个 64 位十六进制密钥
    let mut buf = [0u8; 32];
    // 熵源读取失败不再让整链 ?: 短路：读不到就明确走回退（原 or_else 里 Ok(()) 与
    // Result<File, _> 类型冲突，cargo check E0282）
    let entropy_ok = fs::File::open("/dev/urandom")
        .or_else(|_| {
            #[cfg(windows)]
            {
                fs::File::open(r"\\.\PhysicalDrive0") // 仅为读时间噪声回退，见 TODO
            }
            #[cfg(not(windows))]
            {
                Err(std::io::Error::last_os_error())
            }
        })
        .and_then(|mut f| f.read_exact(&mut buf))
        .is_ok();
    if !entropy_ok {
        // TODO(windows): urandom 不可用时退化到哈希(时间+pid)；上线前应换 getrandom
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        (std::time::SystemTime::now(), std::process::id(), 0x5eedu64).hash(&mut h);
        let v = h.finish();
        for i in 0..32 {
            buf[i] = (v >> (i % 8 * 8)) as u8;
        }
    }
    let secret = buf.iter().map(|b| format!("{b:02x}")).collect::<String>();
    fs::write(&path, &secret).map_err(|e| format!("写 secret.key 失败：{e}"))?;
    Ok(secret)
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// 桌面引导登录令牌：持久化在数据目录，随 sidecar 环境注入，前端用 URL hash 换登录态。
/// 与 APP_SECRET_KEY 分开，避免 JWT 主密钥出现在窗口地址里。
fn ensure_bootstrap_token(dir: &PathBuf) -> Result<String, String> {
    let path = dir.join("desktop-bootstrap.token");
    if let Ok(existing) = fs::read_to_string(&path) {
        let trimmed = existing.trim().to_string();
        if trimmed.len() >= 32 {
            return Ok(trimmed);
        }
    }
    let mut buf = [0u8; 32];
    let entropy_ok = fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .is_ok();
    if !entropy_ok {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        (std::time::SystemTime::now(), std::process::id(), 0xb007u64).hash(&mut h);
        let v = h.finish();
        for i in 0..32 {
            buf[i] = (v >> (i % 8 * 8)) as u8;
        }
    }
    let token = buf.iter().map(|b| format!("{b:02x}")).collect::<String>();
    fs::write(&path, &token).map_err(|e| format!("写 desktop-bootstrap.token 失败：{e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
    }
    Ok(token)
}

fn engine_url(port: u16, token: &str) -> String {
    format!("http://127.0.0.1:{port}/#desktop_token={token}")
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/health/ready");
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(1200))
        .build()
        .map(|c| {
            c.get(url)
                .send()
                .map(|r| r.status().is_success())
                .unwrap_or(false)
        })
        .unwrap_or(false)
}

const SPLASH_HTML: &str = r#"data:text/html;charset=utf-8,<html><head><meta name='viewport' content='width=device-width,initial-scale=1'><style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:%23101418;color:%23e6edf3;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}div{text-align:center;max-width:520px;padding:24px}h1{font-size:20px;font-weight:600}p{color:%239aa7b4;line-height:1.7;font-size:14px}code{background:%231c2430;padding:2px 8px;border-radius:6px;font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:%234c9aff;margin-right:6px;animation:p 1.2s infinite}@keyframes p{0%25,100%25{opacity:.3}50%25{opacity:1}}</style></head><body><div><h1><span class='dot'></span>正在准备本地引擎…</h1><p>首次启动需要 10–60 秒（初始化数据库与演示空间）。<br>完成后会自动进入登录页。<br>登录账号与初始口令见文件：<code>LOGIN_FILE</code></p></div></body></html>"#;

fn spawn_sidecar(app: &tauri::AppHandle, state: &EngineState, dir: &PathBuf, port: u16) -> Result<(), String> {
    let secret = ensure_secret(dir)?;
    let bootstrap = ensure_bootstrap_token(dir)?;
    let frontend_dist = app
        .path()
        .resource_dir()
        .map(|r| r.join("frontend-dist"))
        .unwrap_or_default();
    let bundled_asr = app
        .path()
        .resource_dir()
        .map(|r| r.join("asr-model"))
        .unwrap_or_default();
    let sidecar = app
        .shell()
        .sidecar("techyard-sidecar")
        .map_err(|e| format!("找不到 sidecar：{e}"))?;
    // 语音模型目录固定在数据目录下：与安装位置解耦，升级/重装客户端不必重下模型
    let asr_dir = dir.join("models").join("asr");
    let _ = fs::create_dir_all(&asr_dir);
    let (rx, child) = sidecar
        .envs([
            ("TECHYARD_DATA_DIR", dir.to_string_lossy().to_string()),
            ("TECHYARD_PORT", port.to_string()),
            ("APP_SECRET_KEY", secret),
            ("TECHYARD_DESKTOP_BOOTSTRAP", bootstrap),
            ("BUNDLED_ASR_DIR", bundled_asr.to_string_lossy().to_string()),
            ("FRONTEND_DIST", frontend_dist.to_string_lossy().to_string()),
            ("ASR_MODEL_DIR", asr_dir.to_string_lossy().to_string()),
        ])
        .spawn()
        .map_err(|e| format!("启动本地引擎失败：{e}"))?;
    // 引擎输出落盘（用户可一键导出诊断）；同时把管道读空，避免长时间运行被写满阻塞
    let mut rx = rx;
    let log_path = dir.join("logs").join("engine.log");
    if let Some(parent) = log_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if fs::metadata(&log_path).map(|m| m.len() > 10 * 1024 * 1024).unwrap_or(false) {
        let _ = fs::rename(&log_path, dir.join("logs").join("engine.log.1"));
    }
    thread::spawn(move || {
        use std::io::Write;
        let mut log = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .ok();
        while let Some(event) = rx.blocking_recv() {
            if let tauri_plugin_shell::process::CommandEvent::Stdout(bytes)
            | tauri_plugin_shell::process::CommandEvent::Stderr(bytes) = event
            {
                if let Some(file) = log.as_mut() {
                    let _ = writeln!(file, "{}", String::from_utf8_lossy(&bytes));
                    let _ = file.flush();
                }
            }
        }
    });
    *state.child.lock().unwrap() = Some(child);
    *state.port.lock().unwrap() = Some(port);
    Ok(())
}

/// 导出文件名安全化：只保留文件名部分，去掉路径分隔符与控制字符。
fn safe_export_name(raw: &str) -> String {
    let normalized = raw.replace('\\', "/");
    let base = normalized.rsplit('/').next().unwrap_or("").trim();
    let mut out = String::new();
    for ch in base.chars() {
        if ch.is_control() || matches!(ch, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') {
            out.push('_');
        } else {
            out.push(ch);
        }
    }
    let cleaned = out.trim().trim_matches('.').trim();
    if cleaned.is_empty() {
        "导出文件".to_string()
    } else {
        cleaned.to_string()
    }
}

/// 下载目录内重名自动加序号，绝不覆盖用户已有文件。
fn unique_export_path(dir: &Path, filename: &str) -> PathBuf {
    let candidate = dir.join(filename);
    if !candidate.exists() {
        return candidate;
    }
    let path = Path::new(filename);
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("导出文件");
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
    for index in 1..=999 {
        let name = if ext.is_empty() {
            format!("{stem} ({index})")
        } else {
            format!("{stem} ({index}).{ext}")
        };
        let next = dir.join(name);
        if !next.exists() {
            return next;
        }
    }
    candidate
}

/// 前端导出统一入口：Blob → base64 → 写入用户「下载」目录，返回绝对路径。
#[tauri::command]
fn save_export(
    app: tauri::AppHandle,
    filename: String,
    data_base64: String,
) -> Result<String, String> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};

    let bytes = STANDARD
        .decode(data_base64.as_bytes())
        .map_err(|e| format!("文件内容解码失败：{e}"))?;
    let dir = app
        .path()
        .download_dir()
        .map_err(|e| format!("找不到下载目录：{e}"))?;
    fs::create_dir_all(&dir).map_err(|e| format!("创建下载目录失败：{e}"))?;
    let path = unique_export_path(&dir, &safe_export_name(&filename));
    fs::write(&path, bytes).map_err(|e| format!("写入下载文件失败：{e}"))?;
    Ok(path.display().to_string())
}

/// 打开系统「麦克风」隐私设置页；仅在 macOS 有效，其他平台返回提示。
#[tauri::command]
fn open_mic_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            .spawn()
            .map_err(|e| format!("打开系统设置失败：{e}"))?;
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        Err("请在系统设置中手动允许麦克风权限".to_string())
    }
}

fn open_main_window(app: &tauri::AppHandle, url: &str) -> Result<(), String> {
    let parsed: tauri::Url = url.parse().map_err(|_| format!("非法 URL：{url}"))?;
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
        .title("科技小院")
        .decorations(true)
        .resizable(true)
        .inner_size(1280.0, 820.0)
        .min_inner_size(980.0, 640.0)
        .on_permission_request(|_, kind| match kind {
            tauri::webview::PermissionKind::Microphone => tauri::webview::PermissionResponse::Allow,
            _ => tauri::webview::PermissionResponse::Default,
        })
        .on_download(|webview, event| {
            match event {
                tauri::webview::DownloadEvent::Requested { destination, .. } => {
                    if let Some(name) = destination.file_name().and_then(|s| s.to_str()) {
                        let safe = safe_export_name(name);
                        *destination = destination.with_file_name(safe);
                    }
                }
                tauri::webview::DownloadEvent::Finished {
                    url,
                    path,
                    success,
                } => {
                    let _ = webview.emit(
                        "desktop-download-finished",
                        serde_json::json!({
                            "url": url.to_string(),
                            "path": path.map(|p| p.display().to_string()),
                            "success": success,
                        }),
                    );
                }
                _ => {}
            }
            true
        })
        .build()
        .map(|win| {
            let _ = win.set_focus();
        })
        .map_err(|e| e.to_string())
}

/// 启动失败：原生对话框说明原因，关闭后退出；5 分钟兜底退出，避免无窗口僵死。
fn report_fatal(app: &tauri::AppHandle, msg: String) {
    use tauri_plugin_dialog::DialogExt;
    eprintln!("[startup] {msg}");
    let handle = app.clone();
    app.dialog()
        .message(msg)
        .title("启动失败")
        .show(move |_| {
            handle.exit(1);
        });
    let watchdog = app.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(300));
        watchdog.exit(1);
    });
}

/// 拉起或接管本地引擎；成功返回后窗口已指向引擎页面。失败返回人类可读原因。
fn start_local(app: &tauri::AppHandle, state: &EngineState, dir: &PathBuf) -> Result<(), String> {
    let bootstrap = ensure_bootstrap_token(dir)?;
    // 1) 先探已有引擎（AO 同款复用，避免重复拉起/关窗再开报错）
    for p in PORT_RANGE.clone() {
        if !port_free(p) && health_ok(p) {
            *state.port.lock().unwrap() = Some(p);
            return open_main_window(app, &engine_url(p, &bootstrap));
        }
    }
    // 2) 找空闲端口并拉 sidecar
    let port = PORT_RANGE
        .clone()
        .find(|p| port_free(*p))
        .ok_or(format!("端口段 {PORT_RANGE:?} 全被占用"))?;
    spawn_sidecar(app, state, dir, port)?;

    // 3) splash → 轮询就绪 → 导航到引擎页（后台线程，不阻塞事件循环）
    let splash = WebviewWindowBuilder::new(
        app,
        "splash",
        WebviewUrl::External(
            SPLASH_HTML
                .replace("LOGIN_FILE", &dir.join("desktop-login.txt").display().to_string())
                .parse()
                .map_err(|_| "splash data URL 非法".to_string())?,
        ),
    )
    .title("科技小院")
    .inner_size(560.0, 360.0)
    .resizable(false)
    .decorations(false)
    .build()
    .map_err(|e| e.to_string())?;
    let handle = app.clone();
    let win = splash.clone();
    let dir = dir.clone(); // 线程需要 owned 副本（原引用不满足 'static）
    thread::spawn(move || {
        let deadline = std::time::Instant::now() + Duration::from_secs(HEALTH_TIMEOUT_SECS);
        while std::time::Instant::now() < deadline {
            if health_ok(port) {
                let target = engine_url(port, &bootstrap);
                // 引擎就绪后开正式主窗（带标题栏/红绿灯/可缩放），再关掉无边框过渡页。
                // 不能把过渡页直接导航成主界面，否则会一直是无边框小窗。
                if let Err(e) = open_main_window(&handle, &target) {
                    use tauri_plugin_dialog::DialogExt;
                    let msg = format!("打开主界面失败：{e}");
                    handle.dialog().message(msg).title("启动失败").show(|_| {});
                    return;
                }
                let _ = win.close();
                return;
            }
            thread::sleep(Duration::from_millis(800));
        }
        // 4) 超时：原生对话框给出下一步（失败即教学），绝不静默白屏
        use tauri_plugin_dialog::DialogExt;
        let msg = format!(
            "本地引擎 {HEALTH_TIMEOUT_SECS} 秒内未就绪。\n常见原因：安全软件拦截 sidecar、端口段被占、安装包损坏。\n可尝试：完全退出后重开；仍失败请删除数据目录后重试（会重置本地数据）：\n{}",
            dir.display()
        );
        handle.dialog().message(msg).title("启动失败").show(|_| {});
    });
    Ok(())
}

/// 版本条：当前应用版本（tauri.conf.json version），前端免装 app 插件权限。
#[tauri::command]
fn get_app_version(app: tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

/// 设置页：手动检查更新。返回 null=已是最新；对象=新版信息（同时触发事件+提示条通知）。
#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<Option<UpdateAvailable>, String> {
    let updater = app
        .updater()
        .map_err(|e| format!("初始化更新器失败：{e}"))?;
    let update = updater
        .check()
        .await
        .map_err(|e| format!("检查更新请求失败：{e}"))?;
    Ok(update.map(|u| publish_update(&app, u)))
}

/// 设置页：下载并安装更新，成功后应用自动重启（sidecar 在替换前被级联关闭）。
/// 命令先返回，下载进度走 `app:update-downloaded-bytes` 事件；失败走 `app:update-error` + 原生对话框。
#[tauri::command]
async fn download_and_install(app: tauri::AppHandle) -> Result<(), String> {
    // 缓存命中判断 + 取出都收敛为单表达式，避免 State 临时值生命周期问题
    let cached = app.state::<UpdateState>().pending.lock().unwrap().take();
    let update = match cached {
        Some(u) => u,
        None => {
            // 未经检查直接点「升级」：先查一次拿最新
            let updater = app
                .updater()
                .map_err(|e| format!("初始化更新器失败：{e}"))?;
            updater
                .check()
                .await
                .map_err(|e| format!("检查更新请求失败：{e}"))?
                .ok_or_else(|| "当前已是最新版本，无需升级".to_string())?
        }
    };
    // 1) 流式下载（插件内置 minisign 验签，坏包/伪造包直接报错）
    let dl_app = app.clone();
    let mut downloaded: u64 = 0;
    let bytes = update
        .download(
            move |chunk_len, _content_length| {
                downloaded += chunk_len as u64;
                let _ = dl_app.emit("app:update-downloaded-bytes", downloaded);
            },
            || {},
        )
        .await
        .map_err(|e| format!("下载更新失败：{e}"))?;
    // 2) 安装前先级联关闭引擎 sidecar：安装成功可能直接替换/退出进程，未必走 run() 清理
    if let Some(child) = app.state::<EngineState>().child.lock().unwrap().take() {
        stop_engine(child);
    }
    let _ = app.emit("app:update-installing", ());
    // 3) 安装是重阻塞活：Windows 由安装器接管并自行退出；macOS/Linux 需手动重启新进程
    let install_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || match update.install(&bytes) {
        Ok(()) => {
            #[cfg(not(windows))]
            install_app.restart();
        }
        Err(e) => {
            let msg = format!("升级安装失败：{e}");
            eprintln!("[updater] {msg}");
            let _ = install_app.emit("app:update-error", msg.clone());
            use tauri_plugin_dialog::DialogExt;
            install_app.dialog().message(msg).title("升级失败").show(|_| {});
        }
    });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // 必须最先注册：第二次启动直接聚焦已有窗口
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(EngineState {
            child: Mutex::new(None),
            port: Mutex::new(None),
        })
        .manage(UpdateState {
            pending: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            get_app_version,
            check_for_updates,
            download_and_install,
            save_export,
            open_mic_settings
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let state = app.state::<EngineState>();
            let dir = match data_dir(&handle) {
                Ok(dir) => dir,
                Err(e) => {
                    report_fatal(&handle, e);
                    return Ok(());
                }
            };
            let cfg = client_config(&dir);
            // 启动失败不能把 Err 抛回 Tauri（会在 Objective-C 回调里 panic），
            // 改成原生对话框提示，用户关闭后正常退出。
            let started = if cfg.mode == "remote" && !cfg.url.is_empty() {
                // 连接服务器模式：不拉引擎，直接开窗（与移动/鸿蒙壳同一体验）
                open_main_window(&handle, &cfg.url)
            } else {
                start_local(&handle, &state, &dir)
            };
            if let Err(e) = started {
                report_fatal(&handle, e);
            }
            // 升级静默检查（30 秒后）：本地/远程模式都跑，与启动路径解耦
            spawn_update_watcher(handle.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("构建 Tauri 应用失败")
        .run(|app, event| {
            // 级联退出：关窗/⌘Q/系统退出都要带走引擎进程树（含 PyInstaller 子进程）
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                if let Some(child) = app.state::<EngineState>().child.lock().unwrap().take() {
                    stop_engine(child);
                }
            }
        });
}

#[cfg(test)]
mod export_tests {
    use super::*;

    #[test]
    fn export_name_strips_paths_and_controls() {
        assert_eq!(safe_export_name("../../etc/passwd"), "passwd");
        assert_eq!(safe_export_name("a/b\\c.txt"), "c.txt");
        assert_eq!(safe_export_name(".."), "导出文件");
        assert_eq!(safe_export_name("报告?.md"), "报告_.md");
        assert_eq!(safe_export_name("  "), "导出文件");
    }

    #[test]
    fn export_path_never_overwrites() {
        let dir = std::env::temp_dir().join(format!("kjxy-export-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("a.md"), b"x").unwrap();

        let second = unique_export_path(&dir, "a.md");
        assert_eq!(second.file_name().unwrap().to_str().unwrap(), "a (1).md");
        fs::write(&second, b"y").unwrap();

        let third = unique_export_path(&dir, "a.md");
        assert_eq!(third.file_name().unwrap().to_str().unwrap(), "a (2).md");

        let _ = fs::remove_dir_all(&dir);
    }
}
